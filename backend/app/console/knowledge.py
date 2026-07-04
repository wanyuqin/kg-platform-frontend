"""控制台业务面：知识 CRUD / Markdown 导入 / 模板下载（技术设计文档 七、八）。

submit 流水线全同步：校验 → 敏感检测 → 去重 → 发布，blocking 当场拒收
（响应 status="rejected" + validation[]，7.2）；hash 重复 409。
draft 仅本人可见（ADR-0021）；编辑权 = owner 本人 / domain 管理员 / 平台管理员。
"""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.audit.stats import hits_last_30d
from app.config import get_settings
from app.console import auth
from app.console.router_deps import current_user
from app.console.templates import TEMPLATES
from app.domain.kid import KNOWLEDGE_TYPES
from app.domain.state_machine import Event, Status, transition
from app.pipeline import parser, sensitive, validators
from app.pipeline.publish import DuplicateContent, PublishInput, publish, save_draft
from app.storage.pg.models import (
    ConsoleUser,
    DomainMember,
    ImportBatch,
    ImportItem,
    Knowledge,
    KnowledgeVersion,
)
from app.storage.pg.session import get_session
from app.storage.viking.client import VikingClient, build_uri, get_viking

router = APIRouter()


# ---- 流水线（八）：校验 + 敏感检测，返回统一 validation 列表 ----


def run_pipeline(type_: str, fields: dict[str, str]) -> tuple[list[dict], bool]:
    """返回 (validation, 是否可入库)；敏感命中并入 validation（rule=sensitive）。"""
    findings = [asdict(f) for f in validators.validate(type_, fields)]
    for hit in sensitive.scan("\n".join(fields.values())):
        findings.append(
            {
                "rule": "sensitive",
                "level": "blocking",
                "message": f"命中敏感信息（{hit.rule}）：…{hit.snippet}…，请脱敏后重提",
            }
        )
    blocked = any(f["level"] == "blocking" for f in findings)
    return findings, not blocked


async def _can_edit(session: AsyncSession, user: ConsoleUser, row: Knowledge) -> None:
    """owner 本人 / domain 管理员 / 平台管理员，其余 403（7.2）。"""
    if user.is_platform_admin or row.owner_user_id == user.user_id:
        return
    await auth.require_domain_role(session, user, row.domain_code, {"admin"})


def _knowledge_out(row: Knowledge) -> dict:
    return {
        "kid": row.kid,
        "title": row.title,
        "domain": row.domain_code,
        "type": row.type,
        "tags": row.tags,
        "status": row.status,
        "index_state": row.index_state,
        "version": row.version,
        "owner": row.owner_user_id,
        "source_type": row.source_type,
        "source_ref": row.source_ref,  # 线稿①溯源卡
        "source_url": row.source_url,
        "effective_date": row.effective_date.isoformat(),
        "expire_date": row.expire_date.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


# ---- 知识 CRUD ----


class KnowledgeCreate(BaseModel):
    domain: str
    type: str
    title: str = Field(min_length=1, max_length=256)
    fields: dict[str, str]
    tags: list[str] = []
    owner: str | None = None  # 缺省为提交人
    effective_date: date
    expire_date: date | None = None
    save_mode: str = Field(default="submit", pattern="^(draft|submit)$")


def _to_input(body: KnowledgeCreate, user: ConsoleUser) -> PublishInput:
    return PublishInput(
        domain_code=body.domain,
        type_=body.type,
        title=body.title,
        sections=body.fields,
        tags=body.tags,
        owner_user_id=body.owner or user.user_id,
        source_type="manual",
        source_ref=f"form:{user.user_id}",
        source_url=None,
        effective_date=body.effective_date,
        expire_date=body.expire_date,
        actor_user_id=user.user_id,
    )


@router.post("/knowledge")
async def create_knowledge(
    body: KnowledgeCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    await auth.require_domain_role(session, user, body.domain, {"admin", "member"})
    if body.type not in KNOWLEDGE_TYPES:
        raise errors.invalid_argument(f"unknown type: {body.type}")

    validation, ok = run_pipeline(body.type, body.fields)
    if body.save_mode == "submit" and not ok:
        return {"kid": None, "status": "rejected", "validation": validation}

    inp = _to_input(body, user)
    try:
        if body.save_mode == "draft":
            kid = await save_draft(session, inp)
            return {"kid": kid, "status": "draft", "validation": validation}
        result = await publish(session, viking, inp)
    except DuplicateContent as exc:
        raise errors.conflict(f"内容与已有知识 {exc.existing_kid} 重复")
    return {
        "kid": result.kid,
        "status": result.status,
        "validation": validation,
        "index_state": result.index_state,
    }


@router.get("/knowledge")
async def list_knowledge(
    domain: str | None = None,
    type: str | None = None,  # noqa: A002  与接口契约字段名一致
    status: str | None = None,
    tag: str | None = None,
    owner: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    page_size = min(page_size, 100)
    stmt = select(Knowledge).order_by(Knowledge.updated_at.desc())
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(Knowledge.domain_code.in_(member_domains))
    # draft 仅本人可见（ADR-0021），对任何角色一视同仁
    stmt = stmt.where(
        or_(Knowledge.status != Status.DRAFT, Knowledge.owner_user_id == user.user_id)
    )
    if domain:
        stmt = stmt.where(Knowledge.domain_code == domain)
    if type:
        stmt = stmt.where(Knowledge.type == type)
    if status:
        stmt = stmt.where(Knowledge.status == status)
    if tag:
        stmt = stmt.where(Knowledge.tags.contains([tag]))
    if owner:
        stmt = stmt.where(Knowledge.owner_user_id == owner)
    if q:
        stmt = stmt.where(
            or_(Knowledge.title.ilike(f"%{q}%"), Knowledge.kid.ilike(f"%{q}%"))
        )  # 线稿⑤：搜索标题 / kid
    rows = (
        (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    hits = await hits_last_30d(session, [r.kid for r in rows])
    return {
        "items": [{**_knowledge_out(r), "hits_30d": hits.get(r.kid, 0)} for r in rows],
        "page": page,
        "page_size": page_size,
    }


@router.get("/knowledge/stats")
async def knowledge_stats(
    domain: str | None = None,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """类型维度计数（线稿⑤类型 tab：全部(N)/FAQ(N)/…）。可见性口径与列表一致。"""
    stmt = select(Knowledge.type, func.count()).group_by(Knowledge.type)
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(Knowledge.domain_code.in_(member_domains))
    stmt = stmt.where(
        or_(Knowledge.status != Status.DRAFT, Knowledge.owner_user_id == user.user_id)
    )
    if domain:
        stmt = stmt.where(Knowledge.domain_code == domain)
    rows = (await session.execute(stmt)).all()
    by_type = {type_: n for type_, n in rows}
    return {"total": sum(by_type.values()), "by_type": by_type}


class ValidateBody(BaseModel):
    type: str
    fields: dict[str, str]


@router.post("/knowledge/validate")
async def validate_knowledge(
    body: ValidateBody,
    user: ConsoleUser = Depends(current_user),
):
    """纯校验不落库：线稿③右侧实时完整性校验面板的后端。"""
    if body.type not in KNOWLEDGE_TYPES:
        raise errors.invalid_argument(f"unknown type: {body.type}")
    validation, ok = run_pipeline(body.type, body.fields)
    return {"ok": ok, "validation": validation}


async def _load_visible(session: AsyncSession, user: ConsoleUser, kid: str) -> Knowledge:
    row = await session.get(Knowledge, kid)
    if row is None:
        raise errors.not_found()
    await auth.require_domain_role(session, user, row.domain_code, {"admin", "member"})
    if row.status == Status.DRAFT and row.owner_user_id != user.user_id:
        raise errors.forbidden()  # 草稿仅本人可见（ADR-0021）
    return row


@router.get("/knowledge/{kid}")
async def knowledge_detail(
    kid: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await _load_visible(session, user, kid)
    snaps = (
        (
            await session.execute(
                select(KnowledgeVersion)
                .where(KnowledgeVersion.kid == kid)
                .order_by(KnowledgeVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    current = next((s for s in snaps if s.version == row.version), snaps[0] if snaps else None)
    hits = await hits_last_30d(session, [kid])
    return {
        **_knowledge_out(row),
        "hits_30d": hits.get(kid, 0),  # 线稿①底部治理信息条
        "content": current.content if current else "",
        "fields": (current.meta or {}).get("fields", {}) if current else {},
        "versions": [
            {
                "version": s.version,
                "created_by": s.created_by,
                "created_at": s.created_at.isoformat(),
                "content_hash": s.content_hash,
            }
            for s in snaps
            if s.version >= 1  # version=0 为草稿槽位，不属于版本历史
        ],
    }


@router.put("/knowledge/{kid}")
async def update_knowledge(
    kid: str,
    body: KnowledgeCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    row = await _load_visible(session, user, kid)
    await _can_edit(session, user, row)
    validation, ok = run_pipeline(body.type, body.fields)
    if not ok:
        return {"kid": kid, "status": "rejected", "validation": validation}
    try:
        result = await publish(session, viking, _to_input(body, user), kid=kid)
    except DuplicateContent as exc:
        raise errors.conflict(f"内容与已有知识 {exc.existing_kid} 重复")
    return {
        "kid": kid,
        "status": result.status,
        "version": result.version,
        "validation": validation,
    }


class MetaPatch(BaseModel):
    tags: list[str] | None = None
    owner: str | None = None
    expire_date: date | None = None


@router.patch("/knowledge/{kid}/meta")
async def patch_meta(
    kid: str,
    body: MetaPatch,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await _load_visible(session, user, kid)
    await _can_edit(session, user, row)
    if body.tags is not None:
        row.tags = body.tags
    if body.owner is not None:
        row.owner_user_id = body.owner
    if body.expire_date is not None:
        row.expire_date = body.expire_date
    await session.commit()
    return _knowledge_out(row)


@router.post("/knowledge/{kid}/archive")
async def archive_knowledge(
    kid: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    row = await _load_visible(session, user, kid)
    await _can_edit(session, user, row)
    row.status = transition(Status(row.status), Event.ARCHIVE)  # 非法迁移抛业务异常
    await session.commit()
    await viking.delete(build_uri(row.domain_code, row.type, kid))  # 幂等（404 视为成功）
    return {"kid": kid, "status": row.status}


class RenewBody(BaseModel):
    expire_date: date


@router.post("/knowledge/{kid}/renew")
async def renew_knowledge(
    kid: str,
    body: RenewBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await _load_visible(session, user, kid)
    await _can_edit(session, user, row)
    if row.status == Status.EXPIRED:  # P3 扫描落库后的续期路径
        row.status = transition(Status(row.status), Event.RENEW)
    row.expire_date = body.expire_date
    await session.commit()
    return {"kid": kid, "status": row.status, "expire_date": row.expire_date.isoformat()}


# ---- Markdown 导入（8.1，拆分预览确认页后端） ----


@router.post("/imports")
async def upload_import(
    domain: str = Form(),
    type: str = Form(),  # noqa: A002
    file: UploadFile = File(),
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await auth.require_domain_role(session, user, domain, {"admin", "member"})
    if type not in KNOWLEDGE_TYPES:
        raise errors.invalid_argument(f"unknown type: {type}")
    raw = await file.read()
    if len(raw) > get_settings().upload_max_mb * 1024 * 1024:
        raise errors.invalid_argument(f"文件超过 {get_settings().upload_max_mb}MB 上限")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise errors.invalid_argument("文件必须为 UTF-8 编码")

    batch = ImportBatch(
        domain_code=domain,
        type=type,
        file_name=file.filename or "upload.md",
        created_by=user.user_id,
    )
    session.add(batch)
    await session.flush()
    items = []
    for seq, entry in enumerate(parser.split_entries(text), start=1):
        title, fields = parser.parse_sections(entry)
        if type == "faq" and fields.get("标准问法"):
            title = fields["标准问法"]  # FAQ 以标准问法覆盖（8.1）
        validation, ok = run_pipeline(type, fields)
        items.append(
            ImportItem(
                batch_id=batch.id,
                seq=seq,
                title=title,
                content=entry,
                validation=validation,
                is_valid=ok,
            )
        )
    session.add_all(items)
    await session.commit()
    return _batch_out(batch, items)


def _batch_out(batch: ImportBatch, items: list[ImportItem]) -> dict:
    return {
        "id": batch.id,
        "domain": batch.domain_code,
        "type": batch.type,
        "file_name": batch.file_name,
        "status": batch.status,
        "items": [
            {
                "id": i.id,
                "seq": i.seq,
                "title": i.title,
                "is_valid": i.is_valid,
                "validation": i.validation,
                "result_kid": i.result_kid,
                # 线稿⑦：条目展开显示解析后字段预览（现算，不落库）
                "fields": parser.parse_sections(i.content)[1],
            }
            for i in items
        ],
        "template_url": f"/api/templates/{batch.type}.md",  # 拒收提示引用（设计 3.1）
    }


async def _load_batch(
    session: AsyncSession, user: ConsoleUser, batch_id: int
) -> tuple[ImportBatch, list[ImportItem]]:
    batch = await session.get(ImportBatch, batch_id)
    if batch is None:
        raise errors.not_found()
    await auth.require_domain_role(session, user, batch.domain_code, {"admin", "member"})
    items = (
        (
            await session.execute(
                select(ImportItem).where(ImportItem.batch_id == batch_id).order_by(ImportItem.seq)
            )
        )
        .scalars()
        .all()
    )
    return batch, list(items)


@router.get("/imports/{batch_id}")
async def preview_import(
    batch_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    batch, items = await _load_batch(session, user, batch_id)
    return _batch_out(batch, items)


class ConfirmBody(BaseModel):
    item_ids: list[int]


@router.post("/imports/{batch_id}/confirm")
async def confirm_import(
    batch_id: int,
    body: ConfirmBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    batch, items = await _load_batch(session, user, batch_id)
    by_id = {i.id: i for i in items}
    results = []
    for item_id in body.item_ids:
        item = by_id.get(item_id)
        if item is None:
            results.append({"item_id": item_id, "kid": None, "error": "条目不存在"})
            continue
        if not item.is_valid:
            results.append({"item_id": item_id, "kid": None, "error": "blocking 校验未通过"})
            continue
        title, fields = parser.parse_sections(item.content)
        if batch.type == "faq" and fields.get("标准问法"):
            title = fields["标准问法"]
        inp = PublishInput(
            domain_code=batch.domain_code,
            type_=batch.type,
            title=title or "未命名",
            sections=fields,
            tags=[],
            owner_user_id=user.user_id,
            source_type="markdown",
            source_ref=f"import:{batch.id}:{item.seq}",
            source_url=None,
            effective_date=date.today(),
            expire_date=None,
            actor_user_id=user.user_id,
        )
        try:
            result = await publish(session, viking, inp)
        except DuplicateContent as exc:
            results.append(
                {"item_id": item_id, "kid": None, "error": f"与 {exc.existing_kid} 内容重复"}
            )
            continue
        item.result_kid = result.kid
        results.append({"item_id": item_id, "kid": result.kid, "error": None})
    batch.status = "confirmed"
    await session.commit()
    return {"id": batch.id, "status": batch.status, "results": results}


@router.get("/templates/{type_name}.md")
async def download_template(
    type_name: str,
    user: ConsoleUser = Depends(current_user),  # 登录即可（7.2）
):
    template = TEMPLATES.get(type_name)
    if template is None:
        raise errors.not_found()
    return Response(content=template, media_type="text/markdown; charset=utf-8")
