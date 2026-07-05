"""控制台管理面：domain / 成员 / API Key / 审计查询（技术设计文档 七 7.2）。

domain 注册与 key 签发限平台管理员；成员维护限 domain 管理员（平台旁路）。
"""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.audit.stats import key_usage_last_30d
from app.console import auth
from app.console.router_deps import current_user
from app.gateway.auth import hash_key, new_key_material

from app.storage.pg.models import (
    ApiKey,
    AuditLog,
    ConsoleUser,
    Domain,
    DomainMember,
    Knowledge,
)
from app.storage.pg.session import get_session
from app.storage.redis.client import get_redis

router = APIRouter()

EXPORT_MAX_ROWS = 100_000  # 十一：导出流式返回，单次上限 10 万行


def _domain_out(d: Domain) -> dict:
    return {
        "code": d.code,
        "short_code": d.short_code,
        "name": d.name,
        "default_ttl_days": d.default_ttl_days,
        "type_topk": d.type_topk,
        "created_at": d.created_at.isoformat(),
    }


class DomainCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    short_code: str = Field(pattern=r"^[a-z]{2,4}$")
    name: str = Field(min_length=1, max_length=64)
    default_ttl_days: int = Field(default=365, ge=1)


@router.post("/domains")
async def create_domain(
    body: DomainCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    row = Domain(
        code=body.code,
        short_code=body.short_code,
        name=body.name,
        default_ttl_days=body.default_ttl_days,
        created_by=user.user_id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise errors.conflict("domain code 或 short_code 已存在")
    return _domain_out(row)


async def _domain_stats(session: AsyncSession, codes: list[str]) -> dict[str, dict]:
    """线稿⑥域卡片：知识量、类型分布、Agent（active key）数。"""
    stats = {code: {"total": 0, "by_type": {}, "agents": 0} for code in codes}
    knowledge_rows = await session.execute(
        select(Knowledge.domain_code, Knowledge.type, func.count())
        .where(Knowledge.domain_code.in_(codes), Knowledge.status != "archived")
        .group_by(Knowledge.domain_code, Knowledge.type)
    )
    for code, type_, n in knowledge_rows:
        stats[code]["by_type"][type_] = n
        stats[code]["total"] += n
    key_rows = (
        (await session.execute(select(ApiKey).where(ApiKey.status == "active"))).scalars().all()
    )
    for key in key_rows:
        for code in key.domain_whitelist:
            if code in stats:
                stats[code]["agents"] += 1
    return stats


@router.get("/domains/mine")
async def my_domains(
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """当前用户可见的 domain（线稿⑤域切换器：仅显示有权限的 domain + common）。"""
    stmt = select(Domain).order_by(Domain.code)
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(Domain.code.in_(member_domains) | (Domain.code == "common"))
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_domain_out(d) for d in rows]}


@router.get("/domains")
async def list_domains(
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    rows = (await session.execute(select(Domain).order_by(Domain.code))).scalars().all()
    stats = await _domain_stats(session, [d.code for d in rows])
    return {"items": [{**_domain_out(d), "stats": stats[d.code]} for d in rows]}


def _key_out(
    k: ApiKey,
    *,
    created_by_name: str | None = None,
    usage: dict | None = None,
) -> dict:
    out = {
        "key_id": k.key_id,
        "agent_name": k.agent_name,
        "qps_limit": k.qps_limit,
        "status": k.status,
        "domain_whitelist": k.domain_whitelist,
        "created_at": k.created_at.isoformat(),
        "created_by": k.created_by,
        "created_by_name": created_by_name or k.created_by,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
        "calls_30d": (usage or {}).get("calls_30d", 0),
        "last_used_at": (usage or {}).get("last_used_at"),
    }
    return out


async def _owner_names(session: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(ConsoleUser.user_id, ConsoleUser.name).where(ConsoleUser.user_id.in_(user_ids))
    )
    return {uid: name for uid, name in rows}


async def _keys_out(session: AsyncSession, keys: list[ApiKey]) -> list[dict]:
    if not keys:
        return []
    owner_ids = {k.created_by for k in keys}
    names = await _owner_names(session, owner_ids)
    usage = await key_usage_last_30d(session, [k.key_id for k in keys])
    return [
        _key_out(k, created_by_name=names.get(k.created_by), usage=usage.get(k.key_id))
        for k in keys
    ]


async def _active_key_counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(ApiKey.created_by, func.count())
        .where(ApiKey.status == "active")
        .group_by(ApiKey.created_by)
    )
    return {uid: n for uid, n in rows}


async def _user_domains(session: AsyncSession, user_ids: list[str]) -> dict[str, list[dict]]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(DomainMember.user_id, DomainMember.domain_code, DomainMember.role, Domain.name)
        .join(Domain, Domain.code == DomainMember.domain_code)
        .where(DomainMember.user_id.in_(user_ids))
        .order_by(DomainMember.domain_code)
    )
    result: dict[str, list[dict]] = {uid: [] for uid in user_ids}
    for user_id, code, role, name in rows:
        result[user_id].append({"code": code, "name": name, "role": role})
    return result


def _user_out(
    u: ConsoleUser,
    *,
    domains: list[dict] | None = None,
    active_key_count: int = 0,
) -> dict:
    return {
        "user_id": u.user_id,
        "name": u.name,
        "is_platform_admin": u.is_platform_admin,
        "created_at": u.created_at.isoformat(),
        "domains": domains or [],
        "active_key_count": active_key_count,
    }


async def _normalize_whitelist(session: AsyncSession, whitelist: list[str]) -> list[str]:
    """校验并规范化 domain 白名单：去重、剔除 common（鉴权时自动并入）、校验 domain 存在。"""
    if not whitelist:
        raise errors.invalid_argument("domain_whitelist 不能为空")
    codes: list[str] = []
    seen: set[str] = set()
    for code in whitelist:
        if code == "common" or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise errors.invalid_argument("domain_whitelist 不能只含 common")
    existing = set(
        (await session.execute(select(Domain.code).where(Domain.code.in_(codes)))).scalars()
    )
    unknown = [c for c in codes if c not in existing]
    if unknown:
        raise errors.invalid_argument(f"未知 domain: {', '.join(unknown)}")
    return codes


async def _ensure_no_active_agent(
    session: AsyncSession, agent_name: str, *, exclude_key_id: str | None = None
) -> None:
    stmt = select(ApiKey).where(ApiKey.agent_name == agent_name, ApiKey.status == "active")
    if exclude_key_id:
        stmt = stmt.where(ApiKey.key_id != exclude_key_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        raise errors.conflict(
            f"Agent「{agent_name}」已有启用中的 Key（{existing.key_id}），请先吊销"
        )


async def _invalidate_key_cache(key_id: str) -> None:
    await get_redis().delete(f"ak:{key_id}")


@router.get("/keys")
async def list_keys(
    created_by: str | None = None,
    status: str | None = None,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Agent 中心化 Key 列表（平台管理员）。"""
    auth.require_platform_admin(user)
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    if created_by:
        stmt = stmt.where(ApiKey.created_by == created_by)
    if status:
        stmt = stmt.where(ApiKey.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": await _keys_out(session, rows)}


@router.get("/users")
async def list_users(
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """平台管理员：全量用户列表。"""
    auth.require_platform_admin(user)
    stmt = select(ConsoleUser).order_by(ConsoleUser.created_at.desc())
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(ConsoleUser.name.ilike(pattern), ConsoleUser.user_id.ilike(pattern)))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    user_ids = [u.user_id for u in rows]
    domains_map = await _user_domains(session, user_ids)
    key_counts = await _active_key_counts(session)
    return {
        "items": [
            _user_out(
                u,
                domains=domains_map.get(u.user_id, []),
                active_key_count=key_counts.get(u.user_id, 0),
            )
            for u in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """平台管理员：用户详情（域角色 + 签发的 Key）。"""
    auth.require_platform_admin(user)
    row = await session.get(ConsoleUser, user_id)
    if row is None:
        raise errors.not_found()
    domains_map = await _user_domains(session, [user_id])
    key_counts = await _active_key_counts(session)
    keys = (
        (
            await session.execute(
                select(ApiKey)
                .where(ApiKey.created_by == user_id)
                .order_by(ApiKey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        **_user_out(
            row,
            domains=domains_map.get(user_id, []),
            active_key_count=key_counts.get(user_id, 0),
        ),
        "keys": await _keys_out(session, keys),
    }


class UserPatch(BaseModel):
    is_platform_admin: bool


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatch,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """平台管理员：授予/撤销平台管理员身份。"""
    auth.require_platform_admin(user)
    if user_id == user.user_id and not body.is_platform_admin:
        raise errors.invalid_argument("不能撤销自己的平台管理员身份")
    row = await session.get(ConsoleUser, user_id)
    if row is None:
        raise errors.not_found()
    row.is_platform_admin = body.is_platform_admin
    await session.commit()
    domains_map = await _user_domains(session, [user_id])
    key_counts = await _active_key_counts(session)
    return _user_out(
        row,
        domains=domains_map.get(user_id, []),
        active_key_count=key_counts.get(user_id, 0),
    )


@router.get("/domains/{code}/keys")
async def list_domain_keys(
    code: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """线稿② C 区：Agent 白名单与 Key 表格（掩码信息，不含 hash/明文）。"""
    auth.require_platform_admin(user)
    rows = (
        (
            await session.execute(
                select(ApiKey)
                .where(ApiKey.domain_whitelist.contains([code]))
                .order_by(ApiKey.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {"items": await _keys_out(session, rows)}


@router.get("/domains/{code}/members")
async def list_domain_members(
    code: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """域成员列表（domain 管理员或平台管理员）。"""
    await auth.require_domain_role(session, user, code, {"admin"})
    if await session.get(Domain, code) is None:
        raise errors.not_found()
    rows = await session.execute(
        select(DomainMember.user_id, DomainMember.role, ConsoleUser.name)
        .join(ConsoleUser, ConsoleUser.user_id == DomainMember.user_id)
        .where(DomainMember.domain_code == code)
        .order_by(DomainMember.user_id)
    )
    return {
        "items": [{"user_id": uid, "name": name, "role": role} for uid, role, name in rows]
    }


class DomainPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    default_ttl_days: int | None = Field(default=None, ge=1)
    type_topk: dict[str, int] | None = None


@router.patch("/domains/{code}")
async def patch_domain(
    code: str,
    body: DomainPatch,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    row = await session.get(Domain, code)
    if row is None:
        raise errors.not_found()
    if body.name is not None:
        row.name = body.name
    if body.default_ttl_days is not None:
        row.default_ttl_days = body.default_ttl_days
    if body.type_topk is not None:
        row.type_topk = body.type_topk
    await session.commit()
    return _domain_out(row)


class MemberUpsert(BaseModel):
    user_id: str
    role: str = Field(pattern="^(admin|member)$")


@router.post("/domains/{code}/members")
async def add_member(
    code: str,
    body: MemberUpsert,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await auth.require_domain_role(session, user, code, {"admin"})
    if await session.get(ConsoleUser, body.user_id) is None:
        raise errors.invalid_argument(f"用户 {body.user_id} 尚未登录过控制台")
    existing = await session.get(DomainMember, (code, body.user_id))
    if existing:
        existing.role = body.role
    else:
        session.add(DomainMember(domain_code=code, user_id=body.user_id, role=body.role))
    await session.commit()
    return {"domain": code, "user_id": body.user_id, "role": body.role}


@router.delete("/domains/{code}/members")
async def remove_member(
    code: str,
    user_id: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await auth.require_domain_role(session, user, code, {"admin"})
    await session.execute(
        delete(DomainMember).where(
            DomainMember.domain_code == code, DomainMember.user_id == user_id
        )
    )
    await session.commit()
    return {"removed": user_id}


class KeyCreate(BaseModel):
    agent_name: str = Field(min_length=1, max_length=64)
    qps_limit: int = Field(default=10, ge=1)
    domain_whitelist: list[str] | None = None  # 缺省为路径 domain；不含 common（鉴权自动并入）


class AgentKeyCreate(BaseModel):
    agent_name: str = Field(min_length=1, max_length=64)
    domain_whitelist: list[str] = Field(min_length=1)
    qps_limit: int = Field(default=10, ge=1)


class KeyPatch(BaseModel):
    domain_whitelist: list[str] | None = Field(default=None, min_length=1)
    qps_limit: int | None = Field(default=None, ge=1)


async def _issue_key(
    session: AsyncSession,
    *,
    agent_name: str,
    whitelist: list[str],
    qps_limit: int,
    created_by: str,
) -> tuple[str, str, list[str]]:
    await _ensure_no_active_agent(session, agent_name)
    whitelist = await _normalize_whitelist(session, whitelist)
    key_id, plaintext = new_key_material()
    session.add(
        ApiKey(
            key_id=key_id,
            key_hash=hash_key(plaintext),
            agent_name=agent_name,
            domain_whitelist=whitelist,
            qps_limit=qps_limit,
            created_by=created_by,
        )
    )
    await session.commit()
    return key_id, plaintext, whitelist


@router.post("/keys")
async def issue_agent_key(
    body: AgentKeyCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Agent 中心化签发：一把 Key 授权多个 domain。"""
    auth.require_platform_admin(user)
    key_id, plaintext, whitelist = await _issue_key(
        session,
        agent_name=body.agent_name,
        whitelist=body.domain_whitelist,
        qps_limit=body.qps_limit,
        created_by=user.user_id,
    )
    return {
        "key_id": key_id,
        "plaintext": plaintext,
        "agent_name": body.agent_name,
        "qps_limit": body.qps_limit,
        "domain_whitelist": whitelist,
    }


@router.post("/domains/{code}/keys")
async def issue_key(
    code: str,
    body: KeyCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """单域快捷签发；跨域请走 POST /api/keys。"""
    auth.require_platform_admin(user)
    if await session.get(Domain, code) is None:
        raise errors.not_found()
    whitelist = body.domain_whitelist or [code]
    key_id, plaintext, whitelist = await _issue_key(
        session,
        agent_name=body.agent_name,
        whitelist=whitelist,
        qps_limit=body.qps_limit,
        created_by=user.user_id,
    )
    return {
        "key_id": key_id,
        "plaintext": plaintext,
        "agent_name": body.agent_name,
        "qps_limit": body.qps_limit,
        "domain_whitelist": whitelist,
    }


@router.patch("/keys/{key_id}")
async def patch_key(
    key_id: str,
    body: KeyPatch,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """更新 Key 白名单或 QPS（不重发明文）。"""
    auth.require_platform_admin(user)
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise errors.not_found()
    if row.status != "active":
        raise errors.invalid_argument("只能修改启用中的 Key")
    if body.domain_whitelist is None and body.qps_limit is None:
        raise errors.invalid_argument("至少提供 domain_whitelist 或 qps_limit 之一")
    if body.domain_whitelist is not None:
        row.domain_whitelist = await _normalize_whitelist(session, body.domain_whitelist)
    if body.qps_limit is not None:
        row.qps_limit = body.qps_limit
    await session.commit()
    await _invalidate_key_cache(key_id)
    return (await _keys_out(session, [row]))[0]


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise errors.not_found()
    row.status = "revoked"
    row.revoked_at = datetime.now(tz=None).astimezone()
    await session.commit()
    await _invalidate_key_cache(key_id)  # 主动失效缓存，吊销即时生效（技术 10.1）
    return {"key_id": key_id, "status": "revoked"}


def _audit_query(action: str | None, key_id: str | None, start: str | None, end: str | None):
    stmt = select(AuditLog).order_by(AuditLog.ts.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if key_id:
        stmt = stmt.where(AuditLog.key_id == key_id)
    if start:
        stmt = stmt.where(AuditLog.ts >= datetime.fromisoformat(start))
    if end:
        stmt = stmt.where(AuditLog.ts < datetime.fromisoformat(end))
    return stmt


_AUDIT_FIELDS = (
    "ts",
    "key_id",
    "action",
    "query",
    "filter_type",
    "filter_tag",
    "hits",
    "excluded_expired",
    "kid",
    "version",
    "latency_ms",
)


def _audit_out(r: AuditLog) -> dict:
    out = {f: getattr(r, f) for f in _AUDIT_FIELDS}
    out["ts"] = r.ts.isoformat()
    return out


@router.get("/audit-logs")
async def query_audit_logs(
    action: str | None = None,
    key_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
    page_size: int = 50,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    page_size = min(page_size, 100)
    stmt = _audit_query(action, key_id, start, end).offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_audit_out(r) for r in rows], "page": page, "page_size": page_size}


@router.get("/audit-logs/export")
async def export_audit_logs(
    action: str | None = None,
    key_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    stmt = _audit_query(action, key_id, start, end).limit(EXPORT_MAX_ROWS)
    rows = (await session.execute(stmt)).scalars().all()

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(_AUDIT_FIELDS)
        for r in rows:
            w.writerow([_audit_out(r)[f] for f in _AUDIT_FIELDS])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
        yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-logs.csv"},
    )
