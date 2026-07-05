"""Gateway 对外接口（技术设计文档 六）。

/v1/search 五步流程（6.2）：鉴权限流 → OpenViking 多前缀检索（top_k×3 余量）→
PG 回查过滤（published / 过期剔除计数 / type / tag）→ 排序截断 → 异步审计。
/v1/knowledge/{kid}（6.3）：校验链任一不满足统一 404；content 走 PG 快照（ADR-0018）。
"""

import logging
import time
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.audit import writer
from app.config import get_settings
from app.domain.kid import KNOWLEDGE_TYPES
from app.domain.source import resolve_source_title
from app.gateway.auth import AuthContext, authenticate
from app.storage.pg.models import Domain, Knowledge, KnowledgeVersion, SourceDoc
from app.storage.pg.session import get_session
from app.storage.redis.rate_limit import check_rate_limit
from app.storage.viking.client import VikingClient, VikingError, get_viking

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["gateway"])


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


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise errors.unauthorized()
    return authorization.removeprefix("Bearer ").strip()


async def require_auth(authorization: str | None, session: AsyncSession) -> AuthContext:
    """鉴权（十）+ 限流（10.2）。

    在 endpoint 体内调用而非作为 Depends：FastAPI 依赖先于 body 校验执行，
    作依赖会让"坏参数 + 坏 key"返回 401，破坏"参数校验失败一律 400"的契约（6.1）。
    """
    token = _require_bearer(authorization)
    ctx = await authenticate(session, token)
    if not await check_rate_limit(ctx.key_id, ctx.qps_limit):
        raise errors.rate_limited(ctx.qps_limit)
    return ctx


def _audit(record: dict) -> None:
    record.setdefault("ts", datetime.now(UTC))
    if not writer.enqueue(record):
        logger.warning("audit queue full, record dropped")


async def _effective_top_k(session: AsyncSession, ctx: AuthContext, body: SearchRequest) -> int:
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


def _kid_from_path(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


@router.post("/search")
async def search(
    body: SearchRequest,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    t0 = time.perf_counter()
    ctx = await require_auth(authorization, session)
    top_k = await _effective_top_k(session, ctx, body)
    prefixes = [f"viking://resources/{d}" for d in ctx.domain_whitelist]
    try:
        hits = await viking.search(body.query, prefixes, limit=top_k * 3)  # 重排余量（6.2）
    except VikingError:
        raise errors.upstream_unavailable()

    kids = [_kid_from_path(h["path"]) for h in hits]
    rows = {}
    if kids:
        result = await session.execute(select(Knowledge).where(Knowledge.kid.in_(kids)))
        rows = {row.kid: row for row in result.scalars()}

    today = date.today()
    excluded_expired = 0
    candidates = []
    for h in sorted(hits, key=lambda x: x.get("score") or 0, reverse=True):
        row = rows.get(_kid_from_path(h["path"]))
        if row is None or row.status != "published":
            continue
        if row.domain_code not in ctx.domain_whitelist:  # 防御性：PG 是唯一事实来源
            continue
        if row.expire_date < today:  # 过期兜底剔除（ADR-0020）
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
            "title": row.title,  # title / domain / type 一律以 PG 为准（6.2 第 4 步）
            "summary": h["summary"],
            "uri": h["path"],
            "score": h["score"],
            "domain": row.domain_code,
            "type": row.type,
        }
        for h, row in candidates[:top_k]
    ]
    _audit(
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
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    )
    return {"results": results, "excluded_expired": excluded_expired}


@router.get("/knowledge/{kid}")
async def get_knowledge(
    kid: str,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
):
    t0 = time.perf_counter()
    ctx = await require_auth(authorization, session)
    row = await session.get(Knowledge, kid)
    if (
        row is None
        or row.domain_code not in ctx.domain_whitelist
        or row.status != "published"
        or row.expire_date < date.today()
    ):
        raise errors.not_found()  # 统一 404 不暴露存在性（ADR-0013）

    snap = (
        await session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.kid == kid, KnowledgeVersion.version == row.version
            )
        )
    ).scalar_one()
    doc = await session.get(SourceDoc, row.source_doc_id)
    source_url = row.source_url or (doc.source_url if doc else None)
    _audit(
        {
            "key_id": ctx.key_id,
            "action": "read",
            "kid": kid,
            "version": row.version,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    )
    return {
        "kid": row.kid,
        "title": row.title,
        "domain": row.domain_code,
        "type": row.type,
        "tags": row.tags,
        "version": row.version,
        "content": snap.content,  # PG 快照，不回源 OpenViking（ADR-0018）
        "source": doc.source if doc else None,  # manual | upload | feishu
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
