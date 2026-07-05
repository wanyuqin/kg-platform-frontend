"""审计派生统计（设计 6.3：审计是治理数据主源，不另建埋点体系）。

P1 提供"近 30 天命中"（线稿⑤列表列 / ①治理信息条）；
P3 的零命中清单、报表全集同样由 audit_log 派生。
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.pg.models import AuditLog

WINDOW_DAYS = 30


async def hits_last_30d(session: AsyncSession, kids: list[str]) -> dict[str, int]:
    """近 30 天命中数 = search 命中（hits 展开）+ read 直读，按 kid 聚合。"""
    if not kids:
        return {}
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    counts: dict[str, int] = defaultdict(int)

    read_rows = await session.execute(
        select(AuditLog.kid, func.count())
        .where(AuditLog.action == "read", AuditLog.ts >= since, AuditLog.kid.in_(kids))
        .group_by(AuditLog.kid)
    )
    for kid, n in read_rows:
        counts[kid] += n

    search_stmt = text(
        "SELECT h->>'kid' AS kid, count(*) AS n FROM audit_log "
        "CROSS JOIN LATERAL jsonb_array_elements(hits) h "
        "WHERE action = 'search' AND ts >= :since AND h->>'kid' IN :kids "
        "GROUP BY 1"
    ).bindparams(bindparam("kids", expanding=True))
    search_rows = await session.execute(search_stmt, {"since": since, "kids": kids})
    for kid, n in search_rows:
        counts[kid] += n
    return dict(counts)


async def key_usage_last_30d(
    session: AsyncSession, key_ids: list[str]
) -> dict[str, dict[str, int | str | None]]:
    """近 30 天 API Key 调用量：按 key_id 聚合 count 与 last_used_at。"""
    if not key_ids:
        return {}
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    rows = await session.execute(
        select(AuditLog.key_id, func.count(), func.max(AuditLog.ts))
        .where(AuditLog.key_id.in_(key_ids), AuditLog.ts >= since)
        .group_by(AuditLog.key_id)
    )
    return {
        key_id: {
            "calls_30d": n,
            "last_used_at": last_ts.isoformat() if last_ts else None,
        }
        for key_id, n, last_ts in rows
    }
