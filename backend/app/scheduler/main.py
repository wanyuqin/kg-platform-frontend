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
from app.scheduler import feishu_archive_cleanup, feishu_auth_poll, feishu_poll, jobs, viking_cleanup_retry
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


async def poll_feishu_docs() -> None:
    """飞书文档轮询兜底（feishu-sync §9）。"""
    logger.info("scheduler job start: poll_feishu_docs")
    try:
        async with get_session_factory()() as session:
            n = await feishu_poll.feishu_poll_tick(session)
            await session.commit()
        logger.info("scheduler job done: poll_feishu_docs started=%d", n)
    except Exception:
        logger.exception("scheduler job failed: poll_feishu_docs")
        raise


async def poll_feishu_auth() -> None:
    """飞书授权等待轮询 + 24h 超时（feishu-sync §4.5）。"""
    logger.info("scheduler job start: poll_feishu_auth")
    try:
        async with get_session_factory()() as session:
            n = await feishu_auth_poll.feishu_auth_poll_tick(session)
            await session.commit()
        logger.info("scheduler job done: poll_feishu_auth recovered=%d", n)
    except Exception:
        logger.exception("scheduler job failed: poll_feishu_auth")
        raise


async def purge_feishu_archives() -> None:
    """飞书归档超 30 天物理清理条目（feishu-sync D6）。"""
    logger.info("scheduler job start: purge_feishu_archives")
    try:
        async with get_session_factory()() as session:
            n = await feishu_archive_cleanup.purge_expired_feishu_archives(
                session, get_viking()
            )
            await session.commit()
        logger.info("scheduler job done: purge_feishu_archives purged=%d", n)
    except Exception:
        logger.exception("scheduler job failed: purge_feishu_archives")
        raise


async def retry_viking_cleanup_failed() -> None:
    """重试 OpenViking 删除失败记录（archive purge 兜底）。"""
    logger.info("scheduler job start: retry_viking_cleanup_failed")
    try:
        async with get_session_factory()() as session:
            n = await viking_cleanup_retry.retry_viking_cleanup_failed(session, get_viking())
            await session.commit()
        logger.info("scheduler job done: retry_viking_cleanup_failed recovered=%d", n)
    except Exception:
        logger.exception("scheduler job failed: retry_viking_cleanup_failed")
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
    poll_sec = get_settings().feishu_poll_interval_sec
    scheduler.add_job(poll_feishu_docs, "interval", seconds=poll_sec, id="poll_feishu_docs")
    auth_sec = get_settings().feishu_auth_poll_interval_sec
    scheduler.add_job(poll_feishu_auth, "interval", seconds=auth_sec, id="poll_feishu_auth")
    scheduler.add_job(purge_feishu_archives, "cron", hour=5, id="purge_feishu_archives")
    scheduler.add_job(
        retry_viking_cleanup_failed, "interval", hours=6, id="retry_viking_cleanup_failed"
    )
    return scheduler


async def _main() -> None:
    scheduler = build_scheduler()
    job_ids = ", ".join(j.id for j in scheduler.get_jobs())
    logger.info("scheduler started: timezone=Asia/Shanghai jobs=[%s]", job_ids)
    scheduler.start()
    await asyncio.Event().wait()  # 常驻


if __name__ == "__main__":
    asyncio.run(_main())
