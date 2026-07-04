"""scheduler 进程入口（技术设计文档 二）：单实例、任务幂等。

P1 任务：OpenViking 写入失败重试（8.4）、审计分区预建与清理（十一）。
P3 追加：过期扫描、报表。
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="Asia/Shanghai")


@scheduler.scheduled_job("interval", minutes=1, id="retry_failed_index")
def retry_failed_index() -> None:
    """index_state=failed 的知识重试写入 OpenViking，指数退避、最多 10 次（8.4）。"""
    logger.info("retry_failed_index: not implemented yet")


@scheduler.scheduled_job("cron", day=25, hour=3, id="precreate_audit_partition")
def precreate_audit_partition() -> None:
    """每月 25 日预建下月 audit_log 分区（十一）。"""
    logger.info("precreate_audit_partition: not implemented yet")


@scheduler.scheduled_job("cron", hour=4, id="drop_expired_audit_partition")
def drop_expired_audit_partition() -> None:
    """每日清理超过 KG_AUDIT_RETENTION_DAYS 的分区（十一）。"""
    logger.info("drop_expired_audit_partition: not implemented yet")


if __name__ == "__main__":
    scheduler.start()
