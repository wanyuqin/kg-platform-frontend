"""飞书授权等待状态机（feishu-sync §4.5 / §4.8）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.storage.pg.models import SourceDoc, SyncState

AUTH_WAIT_STATUSES = frozenset({"awaiting_auth", "permission_revoked"})
PERMISSION_REVOKED_ERROR = "permission_revoked: 飞书端已移除机器人权限"
AUTH_TIMEOUT_ERROR = "auth_timeout: 等待飞书授权超过 24 小时"


def had_successful_sync(doc: SourceDoc, sync: SyncState) -> bool:
    """是否曾成功同步过（用于区分 awaiting_auth vs permission_revoked）。"""
    if doc.sync_status == "success":
        return True
    if sync.content_hash or sync.last_sync_at:
        return True
    return sync.sync_status == "idle"


def apply_permission_failure(
    doc: SourceDoc,
    sync: SyncState,
    platform_code: str,
    *,
    now: datetime | None = None,
) -> str:
    """权限预检失败时写入业务状态；返回 doc.sync_status。"""
    now = now or datetime.now(UTC)
    sync.sync_status = "error"
    sync.last_error = platform_code

    if platform_code == "feishu_app_not_in_kb":
        if had_successful_sync(doc, sync) or doc.sync_status == "permission_revoked":
            doc.sync_status = "permission_revoked"
            doc.last_sync_error = PERMISSION_REVOKED_ERROR
        else:
            doc.sync_status = "awaiting_auth"
            doc.last_sync_error = platform_code
        if doc.awaiting_auth_since is None:
            doc.awaiting_auth_since = now
        sync.last_error = doc.last_sync_error
        return doc.sync_status

    doc.sync_status = "failed"
    doc.last_sync_error = platform_code
    doc.awaiting_auth_since = None
    return doc.sync_status


def clear_auth_wait(doc: SourceDoc) -> None:
    """授权恢复或同步成功：清理等待授权状态。"""
    doc.awaiting_auth_since = None
    if doc.sync_status in AUTH_WAIT_STATUSES | {"auth_timeout"}:
        doc.sync_status = "pending"


def mark_auth_timeout(doc: SourceDoc, sync: SyncState) -> None:
    doc.sync_status = "auth_timeout"
    doc.last_sync_error = AUTH_TIMEOUT_ERROR
    sync.sync_status = "error"
    sync.last_error = AUTH_TIMEOUT_ERROR


def is_auth_timed_out(doc: SourceDoc, *, now: datetime, timeout_hours: int) -> bool:
    if doc.awaiting_auth_since is None:
        return False
    elapsed = (now - doc.awaiting_auth_since).total_seconds()
    return elapsed >= timeout_hours * 3600


def should_auth_poll(
    doc: SourceDoc,
    sync: SyncState,
    *,
    now: datetime,
    interval_sec: int,
    timeout_hours: int,
) -> bool:
    if doc.source != "feishu" or doc.status != "active":
        return False
    if doc.sync_status not in AUTH_WAIT_STATUSES:
        return False
    if is_auth_timed_out(doc, now=now, timeout_hours=timeout_hours):
        return False
    if sync.sync_status == "syncing" or doc.sync_status == "syncing":
        return False
    ref = sync.last_auth_check_at or doc.awaiting_auth_since
    if ref is None:
        return True
    return (now - ref).total_seconds() >= interval_sec
