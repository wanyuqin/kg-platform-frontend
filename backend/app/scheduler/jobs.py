"""scheduler 任务体（技术设计文档 8.4 / 十一；草稿清理为 ADR-0021）。

纯 async 函数，session / viking 由调用方注入；全部幂等（单实例进程约束，技术 二）。
重试退避状态存进程内存：scheduler 单点，重启后从头退避可接受。
"""

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.publish import build_frontmatter
from app.storage.pg.models import Knowledge, KnowledgeVersion
from app.storage.viking.client import VikingError, build_uri

logger = logging.getLogger(__name__)

MAX_RETRIES = 10  # 8.4：最多 10 次，仍失败告警平台管理员
DRAFT_TTL_DAYS = 30  # ADR-0021：草稿超期清理

# kid -> (已重试次数, 下次可重试时间)；指数退避 1/2/4/8…分钟
_retry_state: dict[str, tuple[int, datetime]] = {}

_PARTITION_RE = re.compile(r"^audit_log_(\d{4})_(\d{2})$")


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """分区区间边界，沿用迁移脚本的 '+08'（Asia/Shanghai）格式。"""
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}-{month:02d}-01+08", f"{nxt_y:04d}-{nxt_m:02d}-01+08"


async def retry_failed_index(session: AsyncSession, viking: Any) -> int:
    """index_state=failed 的 published 知识重试写入 OpenViking，返回本轮成功数。"""
    rows = (
        (
            await session.execute(
                select(Knowledge).where(
                    Knowledge.index_state == "failed", Knowledge.status == "published"
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    succeeded = 0
    for row in rows:
        count, next_at = _retry_state.get(row.kid, (0, now))
        if count >= MAX_RETRIES:
            logger.error(
                "viking write for %s failed %d times, giving up — 需平台管理员介入（8.4）",
                row.kid,
                count,
            )
            continue
        if now < next_at:
            continue
        snap = (
            await session.execute(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.kid == row.kid,
                    KnowledgeVersion.version == row.version,
                )
            )
        ).scalar_one()
        content = (
            build_frontmatter(
                row.kid, row.title, row.domain_code, row.type, row.tags, row.source_url
            )
            + snap.content
        )
        try:
            await viking.write(build_uri(row.domain_code, row.type, row.kid), content)
        except VikingError:
            backoff_min = min(2**count, 60)
            _retry_state[row.kid] = (count + 1, now + timedelta(minutes=backoff_min))
            logger.warning("viking write retry %d for %s failed", count + 1, row.kid)
            continue
        row.index_state = "indexing"  # ready 由就绪轮询置位
        _retry_state.pop(row.kid, None)
        succeeded += 1
    if succeeded:
        await session.commit()
    return succeeded


async def poll_indexing_ready(session: AsyncSession, viking: Any) -> int:
    """indexing → ready：find probe 命中即就绪（PoC 结论），返回置 ready 数。"""
    rows = (
        (await session.execute(select(Knowledge).where(Knowledge.index_state == "indexing")))
        .scalars()
        .all()
    )
    ready = 0
    for row in rows:
        uri = build_uri(row.domain_code, row.type, row.kid)
        if await viking.is_indexed(uri, probe_query=row.title):
            row.index_state = "ready"
            ready += 1
    if ready:
        await session.commit()
    return ready


async def precreate_audit_partition(session: AsyncSession, today: date) -> str:
    """预建下月 audit_log 分区（幂等），返回分区表名。"""
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    name = f"audit_log_{y:04d}_{m:02d}"
    start, end = _month_bounds(y, m)
    await session.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF audit_log "
            f"FOR VALUES FROM ('{start}') TO ('{end}')"
        )
    )
    await session.commit()
    return name


async def drop_expired_audit_partitions(
    session: AsyncSession, today: date, retention_days: int
) -> list[str]:
    """DROP 覆盖区间整体早于保留期的分区（删分区代替删行，十一），返回被删表名。"""
    cutoff = today - timedelta(days=retention_days)
    rows = await session.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'audit_log_%'")
    )
    dropped = []
    for (name,) in rows:
        m = _PARTITION_RE.match(name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
        partition_end = date(nxt_y, nxt_m, 1)  # 分区覆盖到下月 1 日（不含）
        if partition_end <= cutoff:
            await session.execute(text(f"DROP TABLE {name}"))
            dropped.append(name)
    if dropped:
        await session.commit()
        logger.info("dropped expired audit partitions: %s", ", ".join(dropped))
    return dropped


async def cleanup_stale_drafts(session: AsyncSession) -> int:
    """删除超过 30 天未提交的草稿（ADR-0021），返回删除数。

    draft 无 OpenViking 文件；先删 version=0 草稿正文槽位（外键）再删主行。
    kid 空洞可接受（不复用）。
    """
    cutoff = datetime.now(UTC) - timedelta(days=DRAFT_TTL_DAYS)
    stale = (
        select(Knowledge.kid)
        .where(Knowledge.status == "draft", Knowledge.updated_at < cutoff)
        .scalar_subquery()
    )
    await session.execute(delete(KnowledgeVersion).where(KnowledgeVersion.kid.in_(stale)))
    result = await session.execute(
        delete(Knowledge).where(Knowledge.status == "draft", Knowledge.updated_at < cutoff)
    )
    if result.rowcount:
        await session.commit()
    return result.rowcount
