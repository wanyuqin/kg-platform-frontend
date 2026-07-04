"""API Key 鉴权比对（技术设计文档 十）。

跑在 kg_test 库 + 本地 Redis 上；key_id 随机生成避免缓存串扰。
"""

import secrets

import pytest

from app import errors
from app.gateway.auth import authenticate, hash_key, new_key_material
from app.storage.pg.models import ApiKey
from app.storage.redis.client import get_redis


@pytest.fixture
async def api_key(db_session):
    """签发一把测试 key，返回 (key_id, 明文完整 token)。"""
    key_id, plaintext = new_key_material()
    db_session.add(
        ApiKey(
            key_id=key_id,
            key_hash=hash_key(plaintext),
            agent_name="test-agent",
            domain_whitelist=["free-order"],
            qps_limit=10,
            created_by="test",
        )
    )
    await db_session.commit()
    return key_id, plaintext


class TestKeyMaterial:
    def test_format(self):
        key_id, plaintext = new_key_material()
        assert plaintext.startswith(f"kp_{key_id}_")
        assert len(key_id) == 8
        assert key_id == key_id.lower()

    async def test_secret_with_underscore_authenticates(self, db_session):
        # URL-safe 字符集含 "_"，解析必须 split("_", 2) 而非全量切分
        key_id, _ = new_key_material()
        plaintext = f"kp_{key_id}_abc_def-ghi_jkl"
        db_session.add(
            ApiKey(
                key_id=key_id,
                key_hash=hash_key(plaintext),
                agent_name="underscore-agent",
                domain_whitelist=[],
                created_by="test",
            )
        )
        await db_session.commit()
        ctx = await authenticate(db_session, plaintext)
        assert ctx.key_id == key_id


class TestAuthenticate:
    async def test_valid_key_returns_context(self, db_session, api_key):
        key_id, plaintext = api_key
        ctx = await authenticate(db_session, plaintext)
        assert ctx.key_id == key_id
        assert ctx.qps_limit == 10
        # 白名单自动并入 common（技术 3.2）
        assert set(ctx.domain_whitelist) == {"free-order", "common"}

    async def test_malformed_token_401(self, db_session):
        for bad in ["", "not-a-key", "kp_onlyid", "xx_abcd1234_secret"]:
            with pytest.raises(errors.ApiError) as exc_info:
                await authenticate(db_session, bad)
            assert exc_info.value.http_status == 401

    async def test_unknown_key_id_401(self, db_session):
        with pytest.raises(errors.ApiError) as exc_info:
            await authenticate(db_session, "kp_zzzzzzzz_" + secrets.token_urlsafe(24))
        assert exc_info.value.http_status == 401

    async def test_wrong_secret_401(self, db_session, api_key):
        key_id, _ = api_key
        with pytest.raises(errors.ApiError) as exc_info:
            await authenticate(db_session, f"kp_{key_id}_" + secrets.token_urlsafe(24))
        assert exc_info.value.http_status == 401

    async def test_revoked_key_401(self, db_session, api_key):
        key_id, plaintext = api_key
        row = await db_session.get(ApiKey, key_id)
        row.status = "revoked"
        await db_session.commit()
        with pytest.raises(errors.ApiError) as exc_info:
            await authenticate(db_session, plaintext)
        assert exc_info.value.http_status == 401


class TestCache:
    async def test_cache_populated_after_first_auth(self, db_session, api_key):
        key_id, plaintext = api_key
        await authenticate(db_session, plaintext)
        assert await get_redis().exists(f"ak:{key_id}") == 1

    async def test_served_from_cache_until_invalidated(self, db_session, api_key):
        key_id, plaintext = api_key
        await authenticate(db_session, plaintext)  # 灌缓存
        row = await db_session.get(ApiKey, key_id)
        row.status = "revoked"
        await db_session.commit()
        # 60s TTL 内仍从缓存命中（吊销的即时生效靠主动 DEL，技术 10.1）
        ctx = await authenticate(db_session, plaintext)
        assert ctx.key_id == key_id
        await get_redis().delete(f"ak:{key_id}")
        with pytest.raises(errors.ApiError):
            await authenticate(db_session, plaintext)

    async def test_redis_down_falls_back_to_pg(self, db_session, api_key, monkeypatch):
        key_id, plaintext = api_key

        class BrokenRedis:
            def __getattr__(self, name):
                async def _fail(*a, **kw):
                    from redis.exceptions import RedisError

                    raise RedisError("down")

                return _fail

        monkeypatch.setattr("app.gateway.auth.get_redis", lambda: BrokenRedis())
        ctx = await authenticate(db_session, plaintext)  # 可用性优先：直接回源 PG
        assert ctx.key_id == key_id
