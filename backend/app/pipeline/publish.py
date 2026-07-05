"""发布事务（技术设计文档 8.4）。

发布步骤：PG 事务（取号生成 kid → 写/更新 knowledge → 插入版本快照 → COMMIT）
→ 事务外调用 OpenViking content/write（同 URI 幂等覆盖）→ 写入失败置
index_state=failed 由 scheduler 重试收敛，不回退 published（index_state 与
status 正交，技术 四）。去重先查询友好报错、再靠 uq_knowledge_hash 唯一索引
兜底并发，冲突抛 DuplicateContent（对外 409，设计 4.3.3 第一级漏斗）。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kid import build_kid
from app.domain.state_machine import Event, Status, transition
from app.pipeline.content_hash import SECTION_ORDER, content_hash
from app.storage.pg.models import Domain, Knowledge, KnowledgeVersion
from app.storage.viking.client import VikingClient, VikingError, build_uri


@dataclass
class PublishInput:
    domain_code: str
    type_: str
    title: str
    sections: dict[str, str]
    tags: list[str]
    owner_user_id: str
    source_type: str
    source_ref: str
    source_url: str | None
    effective_date: date
    expire_date: date | None
    actor_user_id: str
    source_doc_id: int | None = None  # 新建行必填；更新已有 kid 时忽略
    doc_seq: int | None = None


@dataclass
class PublishResult:
    kid: str
    version: int
    status: str
    index_state: str


class DuplicateContent(Exception):
    """content_hash 与库内非归档知识重复（对外 409 及已存在 kid）。"""

    def __init__(self, existing_kid: str):
        self.existing_kid = existing_kid
        super().__init__(f"duplicate content of {existing_kid}")


def render_markdown(title: str, type_: str, sections: dict[str, str]) -> str:
    """模板化正文：段按附录 A 顺序重排输出（与 content_hash 规范化同序）。"""
    parts = [f"# {title}"]
    for name in SECTION_ORDER[type_]:
        value = sections.get(name)
        if value is not None and value.strip():
            parts.append(f"## {name}\n{value.strip()}")
    return "\n\n".join(parts) + "\n"


def build_frontmatter(
    kid: str,
    title: str,
    domain_code: str,
    type_: str,
    tags: list[str],
    source_url: str | None,
) -> str:
    """OpenViking 侧冗余字段（技术 九），事实一律以 PG 为准；重试任务复用。"""
    lines = [
        "---",
        f"kid: {kid}",
        f"title: {title}",
        f"domain: {domain_code}",
        f"type: {type_}",
        f"tags: [{', '.join(tags)}]",
        f"source_url: {source_url or ''}",
        f"updated_at: {datetime.now(UTC).isoformat()}",
        "---",
        "",
    ]
    return "\n".join(lines)


async def _load_domain(session: AsyncSession, code: str) -> Domain:
    return (await session.execute(select(Domain).where(Domain.code == code))).scalar_one()


async def _take_seq(session: AsyncSession, domain_code: str, type_: str) -> int:
    """发布事务内取号（技术 5.1）；回滚产生的空洞可接受。"""
    row = await session.execute(
        text(
            "INSERT INTO kid_sequence (domain_code, type, next_seq) VALUES (:d, :t, 2) "
            "ON CONFLICT (domain_code, type) DO UPDATE SET next_seq = kid_sequence.next_seq + 1 "
            "RETURNING next_seq - 1"
        ),
        {"d": domain_code, "t": type_},
    )
    return row.scalar_one()


async def _find_duplicate(session: AsyncSession, hash_: str, exclude_kid: str | None) -> str | None:
    stmt = select(Knowledge.kid).where(
        Knowledge.content_hash == hash_, Knowledge.status != Status.ARCHIVED
    )
    if exclude_kid:
        stmt = stmt.where(Knowledge.kid != exclude_kid)
    return (await session.execute(stmt)).scalars().first()


def _resolve_expire(inp: PublishInput, domain: Domain) -> date:
    return inp.expire_date or inp.effective_date + timedelta(days=domain.default_ttl_days)


async def save_draft(session: AsyncSession, inp: PublishInput) -> str:
    """（新建）→ draft：取号写 knowledge 行。

    版本历史从发布起算（技术 四"快照只在发布时落"），但草稿正文需要
    存储供表单继续编辑——借 knowledge_version 的 version=0 槽位承载，
    发布时删除（不进入版本历史）。
    """
    status = transition(None, Event.SAVE_DRAFT)
    domain = await _load_domain(session, inp.domain_code)
    seq = await _take_seq(session, inp.domain_code, inp.type_)
    kid = build_kid(inp.type_, domain.short_code, seq)
    session.add(
        Knowledge(
            kid=kid,
            title=inp.title,
            domain_code=inp.domain_code,
            type=inp.type_,
            tags=inp.tags,
            source_type=inp.source_type,
            source_ref=inp.source_ref,
            source_url=inp.source_url,
            source_doc_id=inp.source_doc_id,
            doc_seq=inp.doc_seq,
            owner_user_id=inp.owner_user_id,
            status=status,
            effective_date=inp.effective_date,
            expire_date=_resolve_expire(inp, domain),
            content_hash=content_hash(inp.type_, inp.sections),
        )
    )
    session.add(
        KnowledgeVersion(
            kid=kid,
            version=0,  # 草稿正文槽位，发布时删除
            title=inp.title,
            content=render_markdown(inp.title, inp.type_, inp.sections),
            content_hash=content_hash(inp.type_, inp.sections),
            meta={"fields": inp.sections},
            created_by=inp.actor_user_id,
        )
    )
    await session.commit()
    return kid


async def publish(
    session: AsyncSession,
    viking: VikingClient,
    inp: PublishInput,
    kid: str | None = None,
    mode: Literal["publish", "review"] = "publish",
) -> PublishResult:
    """新建发布 / draft 提交发布 / published 内容更新（version+1）。

    mode=review：待审核入库（SUBMIT_RISK），不写 OpenViking，index_state=none。
    返回时 index_state ∈ {indexing, failed, none}；ready 由 scheduler 轮询置位（8.4）。
    """
    domain = await _load_domain(session, inp.domain_code)
    hash_ = content_hash(inp.type_, inp.sections)
    body = render_markdown(inp.title, inp.type_, inp.sections)
    review = mode == "review"

    existing_kid = await _find_duplicate(session, hash_, exclude_kid=kid)
    if existing_kid:
        raise DuplicateContent(existing_kid)

    try:
        if kid is None:
            status = transition(None, Event.SUBMIT_RISK if review else Event.SUBMIT_PASS)
            seq = await _take_seq(session, inp.domain_code, inp.type_)
            kid = build_kid(inp.type_, domain.short_code, seq)
            version = 1
            row = Knowledge(
                kid=kid,
                title=inp.title,
                domain_code=inp.domain_code,
                type=inp.type_,
                tags=inp.tags,
                source_type=inp.source_type,
                source_ref=inp.source_ref,
                source_url=inp.source_url,
                source_doc_id=inp.source_doc_id,
                doc_seq=inp.doc_seq,
                owner_user_id=inp.owner_user_id,
                version=version,
                status=status,
                effective_date=inp.effective_date,
                expire_date=_resolve_expire(inp, domain),
                content_hash=hash_,
                index_state="none" if review else "indexing",
            )
            session.add(row)
        else:
            row = (
                await session.execute(
                    select(Knowledge).where(Knowledge.kid == kid).with_for_update()
                )
            ).scalar_one()
            if review:
                event = Event.SUBMIT_RISK
            else:
                event = Event.SUBMIT_PASS if row.status == Status.DRAFT else Event.UPDATE_CONTENT
            status = transition(Status(row.status), event)
            version = row.version if event == Event.SUBMIT_PASS else row.version + 1
            row.title = inp.title
            row.tags = inp.tags
            if inp.source_url is not None:
                row.source_url = inp.source_url
            row.owner_user_id = inp.owner_user_id
            row.version = version
            row.status = status
            row.effective_date = inp.effective_date
            row.expire_date = _resolve_expire(inp, domain)
            row.content_hash = hash_
            row.index_state = "none" if review else "indexing"
            row.updated_at = func.now()

        # 清理草稿正文槽位（version=0，见 save_draft）
        await session.execute(
            delete(KnowledgeVersion).where(
                KnowledgeVersion.kid == kid, KnowledgeVersion.version == 0
            )
        )
        session.add(
            KnowledgeVersion(
                kid=kid,
                version=version,
                title=inp.title,
                content=body,
                content_hash=hash_,
                meta={
                    "fields": inp.sections,  # 表单编辑回填用
                    "domain": inp.domain_code,
                    "type": inp.type_,
                    "tags": inp.tags,
                    "source_type": inp.source_type,
                    "source_ref": inp.source_ref,
                    "source_url": inp.source_url,
                    "owner_user_id": inp.owner_user_id,
                    "effective_date": inp.effective_date.isoformat(),
                    "expire_date": _resolve_expire(inp, domain).isoformat(),
                    "status": str(status),
                },
                created_by=inp.actor_user_id,
            )
        )
        await session.commit()
    except IntegrityError as exc:
        # 并发兜底：uq_knowledge_hash 唯一索引冲突（技术 8.4）
        await session.rollback()
        existing_kid = await _find_duplicate(session, hash_, exclude_kid=None)
        if existing_kid:
            raise DuplicateContent(existing_kid) from exc
        raise

    index_state = "none" if review else "indexing"
    if not review:
        try:
            await viking.write(
                build_uri(inp.domain_code, inp.type_, kid),
                build_frontmatter(
                    kid, inp.title, inp.domain_code, inp.type_, inp.tags, inp.source_url
                )
                + body,
            )
        except VikingError:
            # 不回退 published：read 走 PG 快照仍可用，search 不可见，scheduler 重试收敛
            index_state = "failed"
            row.index_state = index_state
            await session.commit()

    return PublishResult(kid=kid, version=version, status=str(status), index_state=index_state)
