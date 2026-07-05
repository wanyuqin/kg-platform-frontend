"""scheduler 进程入口（技术设计文档 二）：单实例、任务幂等。

P1 任务：OpenViking 写入失败重试与就绪轮询（8.4）、审计分区预建与清理（十一）、
草稿超期清理（ADR-0021）。P3 追加：过期扫描、报表。
任务体在 jobs.py（纯函数可测）；本文件只做调度装配。
使用 AsyncIOScheduler：任务体是 async（DB / OpenViking），且进程需保持
单一事件循环（engine / redis 连接池绑定 loop）。
"""

import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.scheduler import jobs
from app.storage.pg.session import get_session_factory
from app.storage.viking.client import get_viking

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def retry_failed_index() -> None:
    """index_state=failed 的知识重试写入 OpenViking，指数退避、最多 10 次（8.4）。"""
    logger.info("scheduler job start: retry_failed_index")
    try:
        async with get_session_factory()() as session:
            n = await jobs.retry_failed_index(session, get_viking())
        logger.info("scheduler job done: retry_failed_index recovered=%d", n)
    except Exception:
        logger.exception("scheduler job failed: retry_failed_index")
        raise


async def poll_indexing_ready() -> None:
    """indexing → ready 轮询（8.4，就绪判据见 PoC 结论）。"""
    logger.info("scheduler job start: poll_indexing_ready")
    try:
        async with get_session_factory()() as session:
            n = await jobs.poll_indexing_ready(session, get_viking())
        logger.info("scheduler job done: poll_indexing_ready ready=%d", n)
    except Exception:
        logger.exception("scheduler job failed: poll_indexing_ready")
        raise


async def precreate_audit_partition() -> None:
    """每月 25 日预建下月 audit_log 分区（十一）。"""
    logger.info("scheduler job start: precreate_audit_partition")
    try:
        async with get_session_factory()() as session:
            name = await jobs.precreate_audit_partition(session, today=date.today())
        logger.info("scheduler job done: precreate_audit_partition partition=%s", name)
    except Exception:
        logger.exception("scheduler job failed: precreate_audit_partition")
        raise


async def drop_expired_audit_partition() -> None:
    """每日清理超过 KG_AUDIT_RETENTION_DAYS 的分区（十一）。"""
    logger.info("scheduler job start: drop_expired_audit_partition")
    try:
        async with get_session_factory()() as session:
            dropped = await jobs.drop_expired_audit_partitions(
                session, today=date.today(), retention_days=get_settings().audit_retention_days
            )
        logger.info(
            "scheduler job done: drop_expired_audit_partition dropped=%d names=%s",
            len(dropped),
            ",".join(dropped) if dropped else "-",
        )
    except Exception:
        logger.exception("scheduler job failed: drop_expired_audit_partition")
        raise


async def cleanup_stale_drafts() -> None:
    """每日清理超过 30 天未提交的草稿（ADR-0021）。"""
    logger.info("scheduler job start: cleanup_stale_drafts")
    try:
        async with get_session_factory()() as session:
            n = await jobs.cleanup_stale_drafts(session)
        logger.info("scheduler job done: cleanup_stale_drafts deleted=%d", n)
    except Exception:
        logger.exception("scheduler job failed: cleanup_stale_drafts")
        raise


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(retry_failed_index, "interval", minutes=1, id="retry_failed_index")
    scheduler.add_job(poll_indexing_ready, "interval", seconds=20, id="poll_indexing_ready")
    scheduler.add_job(
        precreate_audit_partition, "cron", day=25, hour=3, id="precreate_audit_partition"
    )
    scheduler.add_job(
        drop_expired_audit_partition, "cron", hour=4, id="drop_expired_audit_partition"
    )
    scheduler.add_job(cleanup_stale_drafts, "cron", hour=4, minute=30, id="cleanup_stale_drafts")
    return scheduler


async def _main() -> None:
    scheduler = build_scheduler()
    job_ids = ", ".join(j.id for j in scheduler.get_jobs())
    logger.info("scheduler started: timezone=Asia/Shanghai jobs=[%s]", job_ids)
    scheduler.start()
    await asyncio.Event().wait()  # 常驻


if __name__ == "__main__":
    asyncio.run(_main())
