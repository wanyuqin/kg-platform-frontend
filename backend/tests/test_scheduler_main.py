"""scheduler 入口：每次任务执行均输出日志。"""

import logging

import pytest

from app.scheduler import main as scheduler_main
from tests.conftest import RecordingViking


class _SessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def mock_session_factory(db_session, monkeypatch):
    def factory():
        return _SessionCtx(db_session)

    monkeypatch.setattr(scheduler_main, "get_session_factory", lambda: factory)
    monkeypatch.setattr(scheduler_main, "get_viking", lambda: RecordingViking().client)


async def test_poll_indexing_ready_logs_start_and_done(mock_session_factory, caplog):
    caplog.set_level(logging.INFO, logger="app.scheduler.main")
    await scheduler_main.poll_indexing_ready()
    messages = [r.message for r in caplog.records if r.name == "app.scheduler.main"]
    assert any("scheduler job start: poll_indexing_ready" in m for m in messages)
    assert any("scheduler job done: poll_indexing_ready ready=0" in m for m in messages)
