"""控制台管理面：domain / 成员 / API Key / 审计查询（技术设计文档 七 7.2）。

domain 注册与 key 签发限平台管理员；成员维护限 domain 管理员（平台旁路）。
"""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.console import auth
from app.console.router_deps import current_user
from app.gateway.auth import hash_key, new_key_material
from sqlalchemy import func

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
    return {
        "items": [
            {
                "key_id": k.key_id,
                "agent_name": k.agent_name,
                "qps_limit": k.qps_limit,
                "status": k.status,
                "domain_whitelist": k.domain_whitelist,
                "created_at": k.created_at.isoformat(),
            }
            for k in rows
        ]
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


@router.post("/domains/{code}/keys")
async def issue_key(
    code: str,
    body: KeyCreate,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    auth.require_platform_admin(user)
    if await session.get(Domain, code) is None:
        raise errors.not_found()
    key_id, plaintext = new_key_material()
    whitelist = body.domain_whitelist or [code]
    session.add(
        ApiKey(
            key_id=key_id,
            key_hash=hash_key(plaintext),
            agent_name=body.agent_name,
            domain_whitelist=whitelist,
            qps_limit=body.qps_limit,
            created_by=user.user_id,
        )
    )
    await session.commit()
    # 明文仅在本响应返回一次（ADR-0012），库内只存 hash
    return {
        "key_id": key_id,
        "plaintext": plaintext,
        "agent_name": body.agent_name,
        "qps_limit": body.qps_limit,
        "domain_whitelist": whitelist,
    }


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
    await get_redis().delete(f"ak:{key_id}")  # 主动失效缓存，吊销即时生效（技术 10.1）
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
