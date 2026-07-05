"""飞书授权状态机单测。"""

from datetime import UTC, datetime, timedelta

from app.feishu.auth_state import (
    AUTH_TIMEOUT_ERROR,
    PERMISSION_REVOKED_ERROR,
    apply_permission_failure,
    clear_auth_wait,
    is_auth_timed_out,
    mark_auth_timeout,
    should_auth_poll,
)
from app.storage.pg.models import SourceDoc, SyncState


def _doc(**kwargs) -> SourceDoc:
    base = dict(
        name="x",
        domain_code="fo",
        type="faq",
        source="feishu",
        status="active",
        sync_status="pending",
        created_by="t",
    )
    base.update(kwargs)
    return SourceDoc(**base)


def _sync(**kwargs) -> SyncState:
    base = dict(
        source_doc_id=1,
        domain_code="fo",
        feishu_doc_token="tok",
        feishu_doc_type="docx",
        registered_by="t",
    )
    base.update(kwargs)
    return SyncState(**base)


class TestApplyPermissionFailure:
    def test_first_bind_awaiting_auth(self):
        doc = _doc()
        sync = _sync()
        status = apply_permission_failure(doc, sync, "feishu_app_not_in_kb")
        assert status == "awaiting_auth"
        assert doc.awaiting_auth_since is not None

    def test_after_success_permission_revoked(self):
        doc = _doc(sync_status="success")
        sync = _sync(content_hash="abc", last_sync_at=datetime.now(UTC))
        status = apply_permission_failure(doc, sync, "feishu_app_not_in_kb")
        assert status == "permission_revoked"
        assert doc.last_sync_error == PERMISSION_REVOKED_ERROR

    def test_other_error_failed(self):
        doc = _doc()
        sync = _sync()
        status = apply_permission_failure(doc, sync, "feishu_doc_not_found")
        assert status == "failed"
        assert doc.awaiting_auth_since is None


class TestAuthPollHelpers:
    def test_clear_auth_wait(self):
        doc = _doc(sync_status="awaiting_auth", awaiting_auth_since=datetime.now(UTC))
        clear_auth_wait(doc)
        assert doc.sync_status == "pending"
        assert doc.awaiting_auth_since is None

    def test_auth_timeout(self):
        now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
        doc = _doc(
            sync_status="awaiting_auth",
            awaiting_auth_since=now - timedelta(hours=25),
        )
        sync = _sync()
        assert is_auth_timed_out(doc, now=now, timeout_hours=24)
        mark_auth_timeout(doc, sync)
        assert doc.sync_status == "auth_timeout"
        assert doc.last_sync_error == AUTH_TIMEOUT_ERROR

    def test_should_auth_poll_respects_interval(self):
        now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
        doc = _doc(
            sync_status="awaiting_auth",
            awaiting_auth_since=now - timedelta(minutes=5),
        )
        sync = _sync(last_auth_check_at=now - timedelta(seconds=30))
        assert should_auth_poll(doc, sync, now=now, interval_sec=60, timeout_hours=24) is False
        sync.last_auth_check_at = now - timedelta(seconds=90)
        assert should_auth_poll(doc, sync, now=now, interval_sec=60, timeout_hours=24) is True
