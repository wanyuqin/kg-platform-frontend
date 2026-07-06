"""控制台飞书同步 API（feishu-sync §13）。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.console import auth
from app.console.router_deps import current_user
from app.console.source_docs import load_doc
from app.feishu.client import FeishuClient
from app.feishu.doc_resolver import PermissionCheck, resolve_with_permission
from app.feishu.event import (
    extract_file_token,
    parse_event_payload,
    verify_event_token,
    verify_signature,
    verify_url_challenge,
)
from app.feishu.event_dispatch import dispatch_feishu_event
from app.feishu.exceptions import FeishuError, FeishuPermissionError
from app.feishu.sync import (
    FeishuSyncError,
    Phase1Result,
    discard_import_batch,
    record_sync_receipt,
    sync_feishu_doc_phase1,
    sync_feishu_doc_phase2,
)
from app.storage.oss.client import OssClient
from app.storage.pg.models import (
    ConsoleUser,
    FeishuSyncReceipt,
    ImportBatch,
    SourceDoc,
    SyncState,
)
from app.storage.pg.session import get_session, get_session_factory
from app.storage.viking.client import VikingClient, get_viking

logger = logging.getLogger(__name__)

router = APIRouter()


def get_feishu_client() -> FeishuClient:
    return FeishuClient()


def get_oss_client() -> OssClient:
    return OssClient()


class ResolveBody(BaseModel):
    feishu_url: str


class CreateFeishuDocBody(BaseModel):
    domain: str
    type: Literal["faq", "sop", "policy", "product", "case", "term"]
    name: str
    feishu_url: str


def _permission_out(perm) -> dict:
    out = {
        "ok": perm.ok,
        "error_code": perm.error_code,
        "error_message": perm.error_message,
    }
    if perm.action_guide:
        out["action_guide"] = perm.action_guide
    return out


def _phase1_out(phase1: Phase1Result) -> dict:
    return {
        "source_doc_id": phase1.source_doc_id,
        "import_batch_id": phase1.import_batch_id,
        "parsed_items": phase1.parsed_items,
        "blocking_count": phase1.blocking_count,
        "skipped_blocks": phase1.skipped_blocks,
        "ok": phase1.ok,
    }


def _sync_status_out(doc: SourceDoc, sync: SyncState | None) -> dict:
    if doc.source != "feishu":
        return {
            "sync_status": None,
            "last_sync_at": None,
            "last_sync_error": None,
            "last_sync_error_detail": None,
        }
    last_at = doc.last_sync_at or (sync.last_sync_at if sync else None)
    error_detail = doc.last_sync_error_detail or (sync.last_error_detail if sync else None)
    return {
        "sync_status": doc.sync_status,
        "technical_status": sync.sync_status if sync else None,
        "last_sync_at": last_at.isoformat() if last_at else None,
        "last_sync_error": doc.last_sync_error or (sync.last_error if sync else None),
        "last_sync_error_detail": error_detail,
        "feishu_url": doc.feishu_url or (sync.feishu_url if sync else None),
        "feishu_doc_token": doc.feishu_doc_token or (sync.feishu_doc_token if sync else None),
        "feishu_doc_type": doc.feishu_doc_type or (sync.feishu_doc_type if sync else None),
        "feishu_title": sync.feishu_title if sync else doc.source_title,
        "content_hash": sync.content_hash if sync else None,
        "source_doc_status": doc.status,
        "archived_at": doc.archived_at.isoformat() if doc.archived_at else None,
        "awaiting_auth_since": doc.awaiting_auth_since.isoformat()
        if doc.awaiting_auth_since
        else None,
        "sync_interval_sec": doc.sync_interval_sec,
    }


async def _load_sync_state(session: AsyncSession, doc_id: int) -> SyncState | None:
    return (
        await session.execute(select(SyncState).where(SyncState.source_doc_id == doc_id))
    ).scalar_one_or_none()


async def _discard_previewing_feishu_batches(session: AsyncSession, doc_id: int) -> None:
    batch_ids = (
        (
            await session.execute(
                select(ImportBatch.id).where(
                    ImportBatch.source_doc_id == doc_id,
                    ImportBatch.origin == "feishu",
                    ImportBatch.status == "previewing",
                )
            )
        )
        .scalars()
        .all()
    )
    for batch_id in batch_ids:
        await discard_import_batch(session, batch_id)


async def _acquire_doc_lock(session: AsyncSession, source_doc_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": source_doc_id},
    )


def _permission_from_error(exc: FeishuPermissionError) -> dict:
    return _permission_out(
        PermissionCheck(
            ok=False,
            error_code=exc.platform_code,
            error_message=str(exc),
            action_guide=exc.action_guide,
        )
    )


async def run_phase2_job(
    source_doc_id: int,
    phase1: Phase1Result,
    actor_user_id: str,
    viking: VikingClient | None = None,
) -> None:
    """阶段二后台任务（独立 session）。"""
    logger.info(
        "feishu phase2 background start doc=%s batch=%s actor=%s",
        source_doc_id,
        phase1.import_batch_id,
        actor_user_id,
    )
    async with get_session_factory()() as session:
        try:
            await sync_feishu_doc_phase2(
                session,
                source_doc_id,
                phase1,
                viking=viking or get_viking(),
                feishu_client=FeishuClient(),
                actor_user_id=actor_user_id,
            )
            await record_sync_receipt(session, source_doc_id, phase1.content_hash, "bind")
            await session.commit()
            logger.info("feishu phase2 background committed doc=%s", source_doc_id)
        except FeishuSyncError as exc:
            if exc.code in {"no_sync_state", "invalid_source", "not_found", "archived"}:
                await discard_import_batch(session, phase1.import_batch_id)
                await session.commit()
                logger.warning(
                    "feishu phase2 aborted doc=%s batch=%s reason=%s",
                    source_doc_id,
                    phase1.import_batch_id,
                    exc.code,
                )
                return
            logger.exception("feishu phase2 background failed doc=%s", source_doc_id)
            await session.rollback()
        except Exception:
            logger.exception("feishu phase2 background failed doc=%s", source_doc_id)
            await session.rollback()


def enqueue_phase2(
    background_tasks: BackgroundTasks,
    source_doc_id: int,
    phase1: Phase1Result,
    actor_user_id: str,
    *,
    viking: VikingClient,
) -> None:
    background_tasks.add_task(run_phase2_job, source_doc_id, phase1, actor_user_id, viking)


@router.post("/source-docs/resolve")
async def resolve_feishu_doc(
    body: ResolveBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    client: FeishuClient = Depends(get_feishu_client),
):
    """绑定前预览：解析飞书 URL + 权限预检（§13.2）。"""
    try:
        resolved, perm = await resolve_with_permission(client, body.feishu_url.strip())
    except FeishuError as exc:
        raise errors.invalid_argument(str(exc)) from exc

    return {
        "resolved": True,
        "feishu_doc_type": resolved.obj_type,
        "feishu_doc_token": resolved.document_token,
        "feishu_url": resolved.doc_url,
        "title": resolved.title,
        "permission_check": _permission_out(perm),
    }


@router.post("/source-docs")
async def create_feishu_source_doc(
    body: CreateFeishuDocBody,
    background_tasks: BackgroundTasks,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    client: FeishuClient = Depends(get_feishu_client),
    oss: OssClient = Depends(get_oss_client),
    viking: VikingClient = Depends(get_viking),
):
    """注册飞书知识文件：权限预检 → 建档 → 阶段一阻塞 → 阶段二后台（§7.3）。"""
    await auth.require_domain_role(session, user, body.domain, {"admin", "member"})

    name = body.name.strip()
    if not name:
        raise errors.invalid_argument("名称不能为空")

    dup = await session.execute(
        select(SourceDoc.id).where(SourceDoc.domain_code == body.domain, SourceDoc.name == name)
    )
    if dup.scalar_one_or_none() is not None:
        raise errors.conflict(f"知识文件「{name}」已存在")

    try:
        resolved, perm = await resolve_with_permission(client, body.feishu_url.strip())
    except FeishuError as exc:
        raise errors.invalid_argument(str(exc)) from exc

    if not perm.ok:
        return JSONResponse(
            status_code=403,
            content={
                "resolved": True,
                "feishu_doc_type": resolved.obj_type,
                "feishu_doc_token": resolved.document_token,
                "feishu_url": resolved.doc_url,
                "title": resolved.title,
                "permission_check": _permission_out(perm),
                "next": "fix permission then retry",
            },
        )

    doc = SourceDoc(
        name=name,
        domain_code=body.domain,
        type=body.type,
        source="feishu",
        source_url=resolved.doc_url,
        source_title=resolved.title or name,
        feishu_doc_token=resolved.document_token,
        feishu_doc_type=resolved.obj_type,
        feishu_url=resolved.doc_url,
        sync_status="pending",
        created_by=user.user_id,
    )
    session.add(doc)
    await session.flush()

    sync = SyncState(
        source_doc_id=doc.id,
        domain_code=body.domain,
        feishu_doc_token=resolved.document_token,
        feishu_doc_type=resolved.obj_type,
        feishu_title=resolved.title,
        feishu_url=resolved.doc_url,
        sync_status="registered",
        registered_by=user.user_id,
    )
    session.add(sync)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise errors.conflict("该飞书文档已在平台注册") from exc

    try:
        phase1 = await sync_feishu_doc_phase1(
            session,
            doc.id,
            client=client,
            oss=oss,
            triggered_by="bind",
            actor_user_id=user.user_id,
        )
    except FeishuPermissionError as exc:
        await session.rollback()
        return JSONResponse(
            status_code=403,
            content={
                "permission_check": _permission_from_error(exc),
                "next": "fix permission then retry",
            },
        )
    except FeishuSyncError as exc:
        await session.rollback()
        raise errors.invalid_argument(exc.args[0]) from exc

    if not phase1.ok:
        await session.commit()
        return JSONResponse(
            status_code=422,
            content={
                "id": doc.id,
                "phase1": _phase1_out(phase1),
                "errors": phase1.validation_errors,
                "next": "wait for content fix",
            },
        )

    await session.commit()
    enqueue_phase2(background_tasks, doc.id, phase1, user.user_id, viking=viking)

    return JSONResponse(
        status_code=201,
        content={
            "id": doc.id,
            "name": doc.name,
            "source": doc.source,
            "phase1": _phase1_out(phase1),
            "sync_status": "syncing",
            "next": "phase2 running",
        },
    )


@router.post("/source-docs/{doc_id}/sync")
async def trigger_feishu_sync(
    doc_id: int,
    background_tasks: BackgroundTasks,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    client: FeishuClient = Depends(get_feishu_client),
    oss: OssClient = Depends(get_oss_client),
    viking: VikingClient = Depends(get_viking),
):
    """手动触发全量同步（§13.2）：阶段一阻塞，阶段二后台（与 bind 一致）。"""
    doc = await load_doc(session, user, doc_id)
    if doc.source != "feishu":
        raise errors.invalid_argument("仅飞书来源文档可手动同步")
    sync = await _load_sync_state(session, doc_id)
    if sync is None:
        raise errors.not_found()

    try:
        phase1 = await sync_feishu_doc_phase1(
            session,
            doc_id,
            client=client,
            oss=oss,
            triggered_by="manual",
            actor_user_id=user.user_id,
        )
    except FeishuPermissionError as exc:
        await session.commit()
        return JSONResponse(
            status_code=403,
            content={
                "permission_check": _permission_from_error(exc),
                "next": "fix permission then retry",
            },
        )
    except FeishuSyncError as exc:
        raise errors.invalid_argument(exc.message) from exc

    if not phase1.ok:
        await session.commit()
        return JSONResponse(
            status_code=422,
            content={
                "phase1": _phase1_out(phase1),
                "errors": phase1.validation_errors,
                "next": "wait for content fix",
            },
        )

    await session.commit()
    enqueue_phase2(background_tasks, doc_id, phase1, user.user_id, viking=viking)

    return {
        "phase1": _phase1_out(phase1),
        "sync_status": "syncing",
        "next": "phase2 running",
    }


@router.post("/source-docs/{doc_id}/unbind-feishu")
async def unbind_feishu_source_doc(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """运营解绑飞书：保留本地条目，转为 manual 来源（feishu-sync §13.3 D8）。"""
    doc = await load_doc(session, user, doc_id)
    if doc.source != "feishu":
        raise errors.invalid_argument("仅飞书来源文档可解绑")

    await _acquire_doc_lock(session, doc_id)
    await session.refresh(doc)
    if doc.sync_status == "syncing":
        raise errors.conflict("文档正在同步中，请稍后再解绑")

    sync = await _load_sync_state(session, doc_id)
    if sync is not None:
        await session.delete(sync)
    await session.execute(
        delete(FeishuSyncReceipt).where(FeishuSyncReceipt.source_doc_id == doc_id)
    )
    await _discard_previewing_feishu_batches(session, doc_id)

    doc.source = "manual"
    doc.feishu_doc_token = None
    doc.feishu_doc_type = None
    doc.feishu_url = None
    doc.sync_status = "pending"
    doc.last_sync_at = None
    doc.last_sync_error = None
    doc.last_sync_error_detail = None
    doc.sync_interval_sec = None
    doc.awaiting_auth_since = None
    doc.updated_at = datetime.now(UTC)

    await session.commit()
    return {
        "id": doc.id,
        "name": doc.name,
        "source": doc.source,
    }


@router.get("/source-docs/{doc_id}/sync-status")
async def feishu_sync_status(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    sync = await _load_sync_state(session, doc_id)
    return _sync_status_out(doc, sync)


@router.get("/source-docs/{doc_id}/sync-history")
async def feishu_sync_history(
    doc_id: int,
    limit: int = 20,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    limit = min(max(limit, 1), 20)
    batches = (
        (
            await session.execute(
                select(ImportBatch)
                .where(ImportBatch.source_doc_id == doc.id, ImportBatch.origin == "feishu")
                .order_by(ImportBatch.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    sync = await _load_sync_state(session, doc_id)
    return {
        "source_doc_id": doc.id,
        "items": [
            {
                "import_batch_id": b.id,
                "status": b.status,
                "created_by": b.created_by,
                "created_at": b.created_at.isoformat(),
            }
            for b in batches
        ],
        "current": _sync_status_out(doc, sync),
    }


@router.post("/feishu/event")
async def feishu_event_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_lark_request_timestamp: Annotated[str | None, Header()] = None,
    x_lark_request_nonce: Annotated[str | None, Header()] = None,
    x_lark_signature: Annotated[str | None, Header()] = None,
):
    """飞书事件回调（§8；公开入口，靠 token/签名校验）。"""
    raw = await request.body()
    verify_signature(
        timestamp=x_lark_request_timestamp or "",
        nonce=x_lark_request_nonce or "",
        body=raw,
        signature=x_lark_signature or "",
    )
    payload = parse_event_payload(raw)
    challenge = verify_url_challenge(payload)
    if challenge is not None:
        return {"challenge": challenge}

    verify_event_token(payload)

    event_type = (payload.get("header") or {}).get("event_type") or payload.get("type")
    file_token = extract_file_token(payload)
    action = await dispatch_feishu_event(session, event_type=event_type, file_token=file_token)
    if action == "error":
        await session.rollback()
    else:
        await session.commit()
    logger.info(
        "feishu event handled type=%s file_token=%s action=%s",
        event_type,
        file_token,
        action,
    )
    return {"ok": True, "action": action}
