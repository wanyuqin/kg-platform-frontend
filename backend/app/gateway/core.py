"""Gateway 核心业务逻辑（HTTP / MCP 共用，ADR-0014 薄封装）。"""

import logging
import time
from datetime import UTC, date, datetime

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.audit import writer
from app.config import get_settings
from app.domain.kid import KNOWLEDGE_TYPES
from app.domain.source import resolve_source_title
from app.gateway.auth import AuthContext
from app.storage.pg.models import Domain, Knowledge, KnowledgeVersion, SourceDoc
from app.storage.viking.client import VikingClient, VikingError

logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    type: list[str] | None = None
    tag: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v

    @field_validator("type")
    @classmethod
    def _known_types(cls, v: list[str] | None) -> list[str] | None:
        if v:
            unknown = [t for t in v if t not in KNOWLEDGE_TYPES]
            if unknown:
                raise ValueError(f"unknown type: {', '.join(unknown)}")
        return v


def audit(record: dict) -> None:
    record.setdefault("ts", datetime.now(UTC))
    if not writer.enqueue(record):
        logger.warning("audit queue full, record dropped")


async def effective_top_k(session: AsyncSession, ctx: AuthContext, body: SearchRequest) -> int:
    """生效值 = min(入参 ?? 类型级配置 ?? 平台默认, 硬上限)（ADR-0011）。"""
    settings = get_settings()
    value = body.top_k
    if value is None and body.type and len(body.type) == 1:
        rows = (
            (
                await session.execute(
                    select(Domain.type_topk).where(Domain.code.in_(ctx.domain_whitelist))
                )
            )
            .scalars()
            .all()
        )
        configured = [tk[body.type[0]] for tk in rows if tk.get(body.type[0])]
        value = max(configured) if configured else None
    return min(value or settings.default_top_k, settings.max_top_k)


def kid_from_path(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


async def do_search(
    session: AsyncSession,
    viking: VikingClient,
    ctx: AuthContext,
    body: SearchRequest,
    *,
    t0: float | None = None,
) -> dict:
    """search 核心逻辑（技术 6.2）。"""
    started = t0 if t0 is not None else time.perf_counter()
    top_k = await effective_top_k(session, ctx, body)
    prefixes = [f"viking://resources/{d}" for d in ctx.domain_whitelist]
    try:
        hits = await viking.search(body.query, prefixes, limit=top_k * 3)
    except VikingError as exc:
        logger.warning("OpenViking search failed: %s", exc)
        raise errors.upstream_unavailable() from exc

    kids = [kid_from_path(h["path"]) for h in hits]
    rows = {}
    if kids:
        result = await session.execute(select(Knowledge).where(Knowledge.kid.in_(kids)))
        rows = {row.kid: row for row in result.scalars()}

    today = date.today()
    excluded_expired = 0
    candidates = []
    for h in sorted(hits, key=lambda x: x.get("score") or 0, reverse=True):
        row = rows.get(kid_from_path(h["path"]))
        if row is None or row.status != "published":
            continue
        if row.domain_code not in ctx.domain_whitelist:
            continue
        if row.expire_date < today:
            excluded_expired += 1
            continue
        if body.type and row.type not in body.type:
            continue
        if body.tag and not set(body.tag) & set(row.tags):
            continue
        candidates.append((h, row))

    results = [
        {
            "kid": row.kid,
            "title": row.title,
            "summary": h["summary"],
            "uri": h["path"],
            "score": h["score"],
            "domain": row.domain_code,
            "type": row.type,
        }
        for h, row in candidates[:top_k]
    ]
    audit(
        {
            "key_id": ctx.key_id,
            "action": "search",
            "query": body.query,
            "filter_type": body.type,
            "filter_tag": body.tag,
            "hits": [
                {"kid": row.kid, "version": row.version, "score": h["score"]}
                for h, row in candidates[:top_k]
            ],
            "excluded_expired": excluded_expired,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    )
    return {"results": results, "excluded_expired": excluded_expired}


async def do_get_knowledge(
    session: AsyncSession,
    ctx: AuthContext,
    kid: str,
    *,
    t0: float | None = None,
) -> dict:
    """read 核心逻辑（技术 6.3）。"""
    started = t0 if t0 is not None else time.perf_counter()
    row = await session.get(Knowledge, kid)
    if (
        row is None
        or row.domain_code not in ctx.domain_whitelist
        or row.status != "published"
        or row.expire_date < date.today()
    ):
        raise errors.not_found()

    snap = (
        await session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.kid == kid, KnowledgeVersion.version == row.version
            )
        )
    ).scalar_one_or_none()
    if snap is None:
        raise errors.not_found()
    doc = await session.get(SourceDoc, row.source_doc_id)
    source_url = row.source_url or (doc.source_url if doc else None)
    audit(
        {
            "key_id": ctx.key_id,
            "action": "read",
            "kid": kid,
            "version": row.version,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    )
    return {
        "kid": row.kid,
        "title": row.title,
        "domain": row.domain_code,
        "type": row.type,
        "tags": row.tags,
        "version": row.version,
        "content": snap.content,
        "source": doc.source if doc else None,
        "source_title": resolve_source_title(doc),
        "source_url": source_url,
        "source_doc": {
            "id": doc.id,
            "name": doc.name,
            "source": doc.source,
            "title": resolve_source_title(doc),
        }
        if doc
        else None,
        "effective_date": row.effective_date.isoformat(),
        "expire_date": row.expire_date.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
