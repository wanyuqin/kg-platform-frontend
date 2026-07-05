"""知识文件（source_doc）查询与操作面（spec §4、§6）。"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import get_settings
from app.console import auth
from app.console.knowledge import _apply_batch_dedup, _batch_out, run_pipeline
from app.console.router_deps import current_user
from app.domain.state_machine import Event, Status, transition
from app.pipeline import parser
from app.pipeline.align import ExistingEntry, align
from app.storage.pg.models import (
    ConsoleUser,
    Domain,
    DomainMember,
    ImportBatch,
    ImportItem,
    Knowledge,
    KnowledgeVersion,
    SourceDoc,
)
from app.storage.pg.session import get_session
from app.storage.viking.client import VikingClient, build_uri, get_viking

router = APIRouter()


def _doc_out(
    doc: SourceDoc,
    total: int = 0,
    published: int = 0,
    index_ready: int = 0,
    index_indexing: int = 0,
    index_failed: int = 0,
) -> dict:
    out = {
        "id": doc.id,
        "name": doc.name,
        "title": doc.source_title or doc.name,
        "domain": doc.domain_code,
        "type": doc.type,
        "source": doc.source,
        "status": doc.status,
        "entry_total": total,
        "entry_published": published,
        "index_ready": index_ready,
        "index_indexing": index_indexing,
        "index_failed": index_failed,
        "updated_at": doc.updated_at.isoformat(),
    }
    if doc.source == "feishu":
        out["sync_status"] = doc.sync_status
        out["feishu_url"] = doc.feishu_url
    return out


async def load_doc(
    session: AsyncSession,
    user: ConsoleUser,
    doc_id: int,
    roles: set[str] = frozenset({"admin", "member"}),
) -> SourceDoc:
    """按 id 取文件；不存在或越权统一 404，不暴露存在性。"""
    doc = await session.get(SourceDoc, doc_id)
    if doc is None:
        raise errors.not_found()
    try:
        await auth.require_domain_role(session, user, doc.domain_code, roles)
    except errors.ApiError as exc:
        if exc.code == "forbidden":
            raise errors.not_found() from exc
        raise
    return doc


def _pub_index_sum(index_state: str):
    return func.sum(
        case(
            (and_(Knowledge.status == Status.PUBLISHED, Knowledge.index_state == index_state), 1),
            else_=0,
        )
    )


def _count_stmt():
    return (
        select(
            Knowledge.source_doc_id,
            func.count().label("total"),
            func.sum(case((Knowledge.status == Status.PUBLISHED, 1), else_=0)).label("published"),
            _pub_index_sum("ready").label("index_ready"),
            _pub_index_sum("indexing").label("index_indexing"),
            _pub_index_sum("failed").label("index_failed"),
        )
        .group_by(Knowledge.source_doc_id)
        .subquery()
    )


@router.get("/source-docs")
async def list_source_docs(
    domain: str | None = None,
    type: str | None = None,  # noqa: A002
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    page_size = min(page_size, 100)
    counts = _count_stmt()
    stmt = (
        select(
            SourceDoc,
            counts.c.total,
            counts.c.published,
            counts.c.index_ready,
            counts.c.index_indexing,
            counts.c.index_failed,
        )
        .outerjoin(counts, counts.c.source_doc_id == SourceDoc.id)
        .order_by(SourceDoc.updated_at.desc())
    )
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(SourceDoc.domain_code.in_(member_domains))
    if domain:
        stmt = stmt.where(SourceDoc.domain_code == domain)
    if type:
        stmt = stmt.where(SourceDoc.type == type)
    if status:
        stmt = stmt.where(SourceDoc.status == status)
    if q:
        stmt = stmt.where(SourceDoc.name.ilike(f"%{q}%"))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).all()
    return {
        "items": [
            _doc_out(
                d,
                t or 0,
                int(p or 0),
                int(ir or 0),
                int(ii or 0),
                int(ifailed or 0),
            )
            for d, t, p, ir, ii, ifailed in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/source-docs/{doc_id}")
async def source_doc_detail(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    entries = (
        (
            await session.execute(
                select(Knowledge)
                .where(Knowledge.source_doc_id == doc_id)
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    batches = (
        (
            await session.execute(
                select(ImportBatch)
                .where(ImportBatch.source_doc_id == doc_id)
                .order_by(ImportBatch.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    stats_rows = (
        await session.execute(
            select(ImportItem.batch_id, ImportItem.align_action, func.count())
            .where(ImportItem.batch_id.in_([b.id for b in batches] or [0]))
            .group_by(ImportItem.batch_id, ImportItem.align_action)
        )
    ).all()
    stats: dict[int, dict[str, int]] = {}
    for bid, action, n in stats_rows:
        stats.setdefault(bid, {})[action] = n
    published = sum(1 for e in entries if e.status == Status.PUBLISHED)
    index_ready = sum(
        1 for e in entries if e.status == Status.PUBLISHED and e.index_state == "ready"
    )
    index_indexing = sum(
        1 for e in entries if e.status == Status.PUBLISHED and e.index_state == "indexing"
    )
    index_failed = sum(
        1 for e in entries if e.status == Status.PUBLISHED and e.index_state == "failed"
    )
    return {
        **_doc_out(
            doc, len(entries), published, index_ready, index_indexing, index_failed
        ),
        "entries": [
            {
                "kid": e.kid,
                "title": e.title,
                "status": e.status,
                "version": e.version,
                "expire_date": e.expire_date.isoformat(),
                "doc_seq": e.doc_seq,
            }
            for e in entries
        ],
        "batches": [
            {
                "id": b.id,
                "origin": b.origin,
                "created_by": b.created_by,
                "created_at": b.created_at.isoformat(),
                "stats": stats.get(b.id, {}),
            }
            for b in batches
        ],
    }


@router.get("/source-docs/{doc_id}/content")
async def source_doc_content(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """全文 = 非 draft / 非 archived 条目当前版本快照按 doc_seq 拼合（spec §2、§4.2）。"""
    doc = await load_doc(session, user, doc_id)
    rows = (
        await session.execute(
            select(KnowledgeVersion.content)
            .join(
                Knowledge,
                (Knowledge.kid == KnowledgeVersion.kid)
                & (Knowledge.version == KnowledgeVersion.version),
            )
            .where(
                Knowledge.source_doc_id == doc_id,
                Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
            )
            .order_by(Knowledge.doc_seq)
        )
    ).scalars()
    return {"name": doc.name, "markdown": "\n\n".join(c.rstrip() + "\n" for c in rows)}


@router.post("/source-docs/{doc_id}/update")
async def update_source_doc(
    doc_id: int,
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """更新知识文件：与新文本对齐，生成待确认批次预览（spec §5、§6）。"""
    doc = await load_doc(session, user, doc_id)
    if doc.status != "active":
        raise errors.conflict("知识文件已归档，不可更新")
    if (file is None) == (text is None):
        raise errors.invalid_argument("file 与 text 必须二选一")
    if file is not None:
        raw = await file.read()
        if len(raw) > get_settings().upload_max_mb * 1024 * 1024:
            raise errors.invalid_argument(f"文件超过 {get_settings().upload_max_mb}MB 上限")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise errors.invalid_argument("文件必须为 UTF-8 编码")
    else:
        content = text

    frontmatter, content = parser.extract_frontmatter(content)
    file_source_url = frontmatter.get("source_url") or None
    file_source_title = frontmatter.get("title") or None

    rows = (
        (
            await session.execute(
                select(Knowledge)
                .where(
                    Knowledge.source_doc_id == doc_id,
                    Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
                )
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    existing = [
        ExistingEntry(
            kid=r.kid,
            title=r.title,
            content_hash=r.content_hash,
            is_form=r.source_ref.startswith("form:"),
        )
        for r in rows
    ]
    aligned = align(doc.type, content, existing)

    batch = ImportBatch(
        domain_code=doc.domain_code,
        type=doc.type,
        file_name=doc.name,
        origin="manual" if text is not None else "upload",
        source_doc_id=doc.id,
        source_url=file_source_url or doc.source_url,
        source_title=file_source_title or doc.source_title,
        created_by=user.user_id,
    )
    session.add(batch)
    await session.flush()
    items = []
    for a in aligned:
        if a.align_action == "disappeared":
            validation, is_valid = [], True
        else:
            _, fields = parser.parse_sections(a.content)
            validation, is_valid = run_pipeline(doc.type, fields)
        items.append(
            ImportItem(
                batch_id=batch.id,
                seq=a.seq,
                title=a.title,
                content=a.content,
                validation=validation,
                is_valid=is_valid,
                align_action=a.align_action,
                match_kid=a.match_kid,
            )
        )
    session.add_all(items)
    await session.flush()
    _apply_batch_dedup(doc.type, items)
    await session.commit()
    form_kids = {r.kid for r in rows if r.source_ref.startswith("form:")}
    return _batch_out(batch, items, form_kids=form_kids)


class RenewDocBody(BaseModel):
    days: int | None = None


@router.post("/source-docs/{doc_id}/renew")
async def renew_source_doc(
    doc_id: int,
    body: RenewDocBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """整体续期：非 draft/archived 条目续期至 today+days（或域默认 TTL）；expired 条目回 published（spec §4.2）。"""
    doc = await load_doc(session, user, doc_id)
    if doc.status != "active":
        raise errors.conflict("知识文件已归档，不可续期")
    domain = await session.get(Domain, doc.domain_code)
    new_expire = date.today() + timedelta(days=body.days or domain.default_ttl_days)
    rows = (
        (
            await session.execute(
                select(Knowledge).where(
                    Knowledge.source_doc_id == doc_id,
                    Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.status == Status.EXPIRED:
            row.status = transition(Status(row.status), Event.RENEW)
        row.expire_date = new_expire
    await session.commit()
    return {"renewed": len(rows), "expire_date": new_expire.isoformat()}


@router.post("/source-docs/{doc_id}/offline")
async def offline_source_doc(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    """整体下架：published/expired 条目 ARCHIVE，文件置 archived；先 commit 再删索引（幂等重试惯例）。"""
    # 有意不加 active 守卫：重复下架幂等成功（archived_entries: 0），与条目级下架的幂等语义一致
    doc = await load_doc(session, user, doc_id)
    rows = (
        (
            await session.execute(
                select(Knowledge).where(
                    Knowledge.source_doc_id == doc_id,
                    Knowledge.status.in_([Status.PUBLISHED, Status.EXPIRED]),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = transition(Status(row.status), Event.ARCHIVE)
    doc.status = "archived"
    await session.commit()
    for row in rows:
        await viking.delete(build_uri(row.domain_code, row.type, row.kid))  # 幂等：404 视为成功
    return {"archived_entries": len(rows)}


class RenameBody(BaseModel):
    name: str


@router.patch("/source-docs/{doc_id}")
async def rename_source_doc(
    doc_id: int,
    body: RenameBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """重命名知识文件；同域同名冲突 409（spec §3.1 唯一约束）。"""
    doc = await load_doc(session, user, doc_id)
    if doc.status != "active":
        raise errors.conflict("知识文件已归档，不可重命名")
    name = body.name.strip()
    if not name:
        raise errors.invalid_argument("名称不能为空")
    dup = await session.execute(
        select(SourceDoc.id).where(
            SourceDoc.domain_code == doc.domain_code,
            SourceDoc.name == name,
            SourceDoc.id != doc_id,
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise errors.conflict(f"知识文件「{name}」已存在")
    doc.name = name
    await session.commit()
    return {"id": doc.id, "name": doc.name}
