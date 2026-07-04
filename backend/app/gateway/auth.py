"""API Key 鉴权（技术设计文档 十）。

明文格式 kp_{key_id}_{secret}：key_id 8 位小写 base32，secret 32 位 URL-safe
随机串（字符集含 "_"，解析用 split("_", 2)）。库内仅存 SHA-256(完整明文)。
校验路径：解析 key_id → Redis 缓存 ak:{key_id}（TTL 60s，miss 回源 PG）→
常数时间比对 → status=active。Redis 故障直接回源 PG（可用性优先，同限流哲学）。
吊销即时生效 = 置 revoked + 主动 DEL 缓存（60s TTL 只是兜底）。
"""

import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.storage.pg.models import ApiKey
from app.storage.redis.client import get_redis

logger = logging.getLogger(__name__)

_BASE32_LOWER = "abcdefghijklmnopqrstuvwxyz234567"
_CACHE_TTL_S = 60


@dataclass
class AuthContext:
    key_id: str
    agent_name: str
    domain_whitelist: list[str]  # 已并入 common
    qps_limit: int


def new_key_material() -> tuple[str, str]:
    """生成 (key_id, 完整明文 token)；明文仅在签发响应返回一次（ADR-0012）。"""
    key_id = "".join(secrets.choice(_BASE32_LOWER) for _ in range(8))
    secret = secrets.token_urlsafe(24)  # 32 字符 URL-safe
    return key_id, f"kp_{key_id}_{secret}"


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _parse_key_id(token: str) -> str:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != "kp" or not parts[1] or not parts[2]:
        raise errors.unauthorized()
    return parts[1]


async def _load_record(session: AsyncSession, key_id: str) -> dict | None:
    """读缓存，miss 或 Redis 故障回源 PG；返回缓存结构 dict 或 None（不存在）。"""
    cache_key = f"ak:{key_id}"
    try:
        cached = await get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except RedisError:
        logger.warning("api key cache degraded: redis unavailable, falling back to PG")

    row = (
        await session.execute(select(ApiKey).where(ApiKey.key_id == key_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    record = {
        "key_hash": row.key_hash,
        "agent_name": row.agent_name,
        "domain_whitelist": list(row.domain_whitelist),
        "qps_limit": row.qps_limit,
        "status": row.status,
    }
    try:
        await get_redis().set(cache_key, json.dumps(record), ex=_CACHE_TTL_S)
    except RedisError:
        pass
    return record


async def authenticate(session: AsyncSession, token: str) -> AuthContext:
    """校验失败一律 errors.unauthorized()（401，不区分原因）。"""
    key_id = _parse_key_id(token)
    record = await _load_record(session, key_id)
    if record is None:
        raise errors.unauthorized()
    if not hmac.compare_digest(record["key_hash"], hash_key(token)):
        raise errors.unauthorized()
    if record["status"] != "active":
        raise errors.unauthorized()
    whitelist = list(record["domain_whitelist"])
    if "common" not in whitelist:  # 白名单不含 common，鉴权时自动并入（技术 3.2）
        whitelist.append("common")
    return AuthContext(
        key_id=key_id,
        agent_name=record["agent_name"],
        domain_whitelist=whitelist,
        qps_limit=record["qps_limit"],
    )
