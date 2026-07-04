"""知识文件（source_doc）查询与操作面（spec §4、§6）。"""

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.console import auth
from app.console.router_deps import current_user
from app.domain.state_machine import Status
from app.storage.pg.models import (
    ConsoleUser,
    DomainMember,
    ImportBatch,
    ImportItem,
    Knowledge,
    KnowledgeVersion,
    SourceDoc,
)
from app.storage.pg.session import get_session

router = APIRouter()


def _doc_out(doc: SourceDoc, total: int = 0, published: int = 0) -> dict:
    return {
        "id": doc.id,
        "name": doc.name,
        "domain": doc.domain_code,
        "type": doc.type,
        "source": doc.source,
        "status": doc.status,
        "entry_total": total,
        "entry_published": published,
        "updated_at": doc.updated_at.isoformat(),
    }


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


def _count_stmt():
    return (
        select(
            Knowledge.source_doc_id,
            func.count().label("total"),
            func.sum(case((Knowledge.status == Status.PUBLISHED, 1), else_=0)).label("published"),
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
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    counts = _count_stmt()
    stmt = (
        select(SourceDoc, counts.c.total, counts.c.published)
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
    rows = (await session.execute(stmt)).all()
    return {"items": [_doc_out(d, t or 0, int(p or 0)) for d, t, p in rows]}


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
    return {
        **_doc_out(doc, len(entries), published),
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
