"""OpenViking 删除失败重试 scheduler 单测。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.scheduler.viking_cleanup_retry import retry_viking_cleanup_failed
from app.storage.pg.models import VikingCleanupFailed
from tests.conftest import RecordingViking


class TestVikingCleanupRetry:
    async def test_retry_success_removes_row(self, db_session):
        uri = "viking://free-order/faq/faq-fo-retry1"
        now = datetime.now(UTC)
        db_session.add(
            VikingCleanupFailed(
                uri=uri,
                last_error="boom",
                retry_count=0,
                next_retry_at=now - timedelta(minutes=1),
            )
        )
        await db_session.commit()

        n = await retry_viking_cleanup_failed(db_session, RecordingViking().client, now=now)
        await db_session.commit()

        assert n == 1
        assert (
            await db_session.execute(
                select(VikingCleanupFailed).where(VikingCleanupFailed.uri == uri)
            )
        ).scalar_one_or_none() is None

    async def test_retry_failure_increments_count(self, db_session):
        import httpx

        from app.storage.viking.client import VikingClient

        uri = "viking://free-order/faq/faq-fo-retry2"
        now = datetime.now(UTC)
        db_session.add(
            VikingCleanupFailed(
                uri=uri,
                last_error="old",
                retry_count=0,
                next_retry_at=now - timedelta(minutes=1),
            )
        )
        await db_session.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "DELETE":
                raise RuntimeError("still down")
            return httpx.Response(200, json={"status": "ok"})

        viking = VikingClient(transport=httpx.MockTransport(handler))
        n = await retry_viking_cleanup_failed(db_session, viking, now=now)
        await db_session.commit()

        assert n == 0
        row = (
            await db_session.execute(
                select(VikingCleanupFailed).where(VikingCleanupFailed.uri == uri)
            )
        ).scalar_one()
        assert row.retry_count == 1
        assert row.last_error == "still down"
