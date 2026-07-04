"""测试基建：PG 集成测试用独立 kg_test 库（技术 13.1，依赖 docker compose 的本地 PG）。

- 整个测试进程的 KG_DATABASE_URL 指向 kg_test，避免污染开发库；
- session 级 fixture 建库并跑 alembic 迁移（含 common 域种子）；
- 每个用例跑在外层事务的 savepoint 里，结束整体回滚，用例间互不可见；
- 本地 PG 不可达时自动 skip DB 用例，纯逻辑单测不受影响。
"""

import os

# 必须在 import app.* 之前生效：get_settings 是进程级缓存
TEST_DB = "kg_test"
TEST_DB_URL = f"postgresql+asyncpg://kg:kg@localhost:5433/{TEST_DB}"
os.environ["KG_DATABASE_URL"] = TEST_DB_URL

import json  # noqa: E402  环境变量必须先于 app 模块导入生效

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


class RecordingViking:
    """包一层 MockTransport 的真实 VikingClient，记录 write / delete 调用（发布链路测试共用）。"""

    def __init__(self, fail: bool = False):
        from app.storage.viking.client import VikingClient

        self.writes: list[dict] = []
        self.deletes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if fail:
                return httpx.Response(500, json={"status": "error"})
            if request.method == "DELETE":
                self.deletes.append(httpx.QueryParams(request.url.query).get("uri"))
            elif request.content:
                self.writes.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok", "result": {}})

        self.client = VikingClient(transport=httpx.MockTransport(handler))


def _prepare_test_db() -> str | None:
    """建 kg_test 库并迁移到 head；返回 None 表示成功，否则返回 skip 原因。"""
    import psycopg

    try:
        with psycopg.connect(
            "postgresql://kg:kg@localhost:5433/kg", autocommit=True, connect_timeout=3
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
            ).fetchone()
            if not exists:
                conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError as exc:
        return f"本地 PG 不可达（docker compose 未启动？）: {exc}"

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    return None


@pytest.fixture(scope="session")
def migrated_db() -> None:
    reason = _prepare_test_db()
    if reason:
        pytest.skip(reason)


@pytest_asyncio.fixture(autouse=True)
async def _reset_loop_bound_singletons():
    """redis 连接池与 asyncio.Queue 的 waiter 都绑定创建/首次 await 时的事件循环，
    而 pytest-asyncio 每个用例一个 loop——每次重置，避免跨 loop 复用报
    RuntimeError（仅测试问题，生产进程单 loop 无此情况）。"""
    import asyncio as _asyncio

    from app.audit import writer as audit_writer
    from app.storage.pg import session as pg_session
    from app.storage.redis import client as redis_client

    redis_client._client = None
    pg_session._engine = None
    pg_session._session_factory = None
    audit_writer._queue = _asyncio.Queue(maxsize=audit_writer.QUEUE_MAXSIZE)
    yield
    if redis_client._client is not None:
        try:
            await redis_client._client.aclose()
        except Exception:
            pass
        redis_client._client = None


@pytest_asyncio.fixture
async def db_session(migrated_db):
    """savepoint 隔离的 AsyncSession：被测代码内部的 commit 只提交 savepoint，
    fixture 结束回滚外层事务，用例间数据互不可见。"""
    engine = create_async_engine(TEST_DB_URL)
    async with engine.connect() as conn:
        outer = await conn.begin()
        factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with factory() as session:
            yield session
        await outer.rollback()
    await engine.dispose()
