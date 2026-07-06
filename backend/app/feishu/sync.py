"""飞书文档同步主流程编排（feishu-sync §7）。"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.state_machine import Event, InvalidTransition, Status, transition
from app.feishu.client import FeishuClient
from app.feishu.doc_resolver import check_permission, resolve_doc
from app.feishu.docx_to_markdown import blocks_to_markdown
from app.feishu.auth_state import apply_permission_failure, clear_auth_wait
from app.feishu.exceptions import FeishuError, FeishuPermissionError
from app.feishu.media import resolve_media_in_markdown
from app.feishu.card import send_review_card
from app.feishu.risk_matrix import (
    RiskMatrixInput,
    publish_mode_for_risk,
    risk_note_for_score,
    score_risk,
)
from app.pipeline import parser
from app.pipeline.align import ExistingEntry, align
from app.pipeline.publish import DuplicateContent, PublishInput, publish
from app.pipeline.validators import validate
from app.pipeline import sensitive
from app.storage.oss.client import OssClient
from app.storage.pg.models import (
    Domain,
    FeishuSyncReceipt,
    ImportBatch,
    ImportItem,
    Knowledge,
    ReviewTask,
    SourceDoc,
    SyncState,
)
from app.storage.viking.client import VikingClient, build_uri

logger = logging.getLogger(__name__)

TriggerKind = Literal["event", "poll", "manual", "bind"]

_TECH_TO_BIZ = {
    "registered": "pending",
    "syncing": "syncing",
    "idle": "success",
    "error": "failed",
    "archived": "archived",
}


def mirror_feishu_metadata(doc: SourceDoc, sync: SyncState) -> None:
    """把 sync_state 飞书元数据镜像到 source_doc（0006）。"""
    doc.feishu_doc_token = sync.feishu_doc_token
    doc.feishu_doc_type = sync.feishu_doc_type
    doc.feishu_url = sync.feishu_url or doc.source_url


def mirror_business_status(doc: SourceDoc, sync: SyncState) -> None:
    """技术状态 → source_doc 业务状态（D4）。"""
    doc.sync_status = _TECH_TO_BIZ.get(sync.sync_status, sync.sync_status)
    doc.last_sync_at = sync.last_sync_at
    doc.last_sync_error = sync.last_error
    doc.last_sync_error_detail = sync.last_error_detail


_PHASE2_REASON_LABELS = {
    "duplicate_content": "与库内已有知识正文重复",
    "validation": "模板校验未通过",
    "invalid_transition": "当前状态不允许发布或更新",
    "disappeared_invalid_transition": "消失条目归档时状态不允许",
    "disappeared_viking_delete": "消失条目删除索引失败",
}


def _phase2_error_message(failed: int, fail_reasons: dict[str, int]) -> str:
    parts = [
        f"{fail_reasons[reason]} {count} 条"
        for reason, count in sorted(fail_reasons.items())
        if count and reason in _PHASE2_REASON_LABELS
    ]
    summary = "；".join(parts) if parts else f"{failed} 条失败"
    return f"同步失败：共 {failed} 条条目未入库（{summary}）"


async def _duplicate_entry_info(session: AsyncSession, kid: str) -> dict:
    row = await session.get(Knowledge, kid)
    if row is None:
        return {"kid": kid}
    info = {
        "kid": row.kid,
        "title": row.title,
        "status": row.status,
        "source_doc_id": row.source_doc_id,
    }
    if row.source_doc_id:
        doc = await session.get(SourceDoc, row.source_doc_id)
        if doc:
            info["source_doc_name"] = doc.name
    return info


def _record_phase2_failure(
    failure_items: list[dict],
    fail_reasons: dict[str, int],
    *,
    seq: int,
    reason: str,
    title: str | None = None,
    align_action: str | None = None,
    extra: dict | None = None,
) -> None:
    fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
    item: dict = {
        "seq": seq,
        "reason": reason,
        "reason_label": _PHASE2_REASON_LABELS.get(reason, reason),
        "title": title,
        "align_action": align_action,
    }
    if extra:
        item.update(extra)
    failure_items.append(item)


def _apply_phase2_sync_error(
    sync: SyncState,
    doc: SourceDoc,
    *,
    message: str,
    detail: dict,
) -> None:
    sync.last_error = message
    sync.last_error_detail = detail
    mirror_business_status(doc, sync)


def _log_phase2_item_failure(
    source_doc_id: int,
    *,
    seq: int,
    reason: str,
    align_action: str | None = None,
    kid: str | None = None,
    title: str | None = None,
    detail: str | None = None,
) -> None:
    logger.warning(
        "feishu phase2 item failed doc=%s seq=%s reason=%s align=%s kid=%s title=%s detail=%s",
        source_doc_id,
        seq,
        reason,
        align_action or "-",
        kid or "-",
        (title or "-")[:80],
        detail or "-",
    )


async def discard_import_batch(session: AsyncSession, batch_id: int) -> None:
    """放弃 previewing 批次（phase2 无法继续时清理孤儿 batch）。"""
    batch = await session.get(ImportBatch, batch_id)
    if batch is not None and batch.status == "previewing":
        batch.status = "discarded"


async def mark_sync_technical_error(
    session: AsyncSession, source_doc_id: int, code: str, *, technical_status: str = "error"
) -> None:
    """同步失败：写 sync_state 并镜像 source_doc。"""
    sync = (
        await session.execute(select(SyncState).where(SyncState.source_doc_id == source_doc_id))
    ).scalar_one_or_none()
    if sync is None:
        return
    doc = await session.get(SourceDoc, source_doc_id)
    sync.sync_status = technical_status
    sync.last_error = code
    sync.last_error_detail = None
    if doc:
        mirror_business_status(doc, sync)


async def record_sync_receipt(
    session: AsyncSession,
    source_doc_id: int,
    content_hash: str,
    triggered_by: TriggerKind,
) -> None:
    existing = (
        await session.execute(
            select(FeishuSyncReceipt).where(
                FeishuSyncReceipt.source_doc_id == source_doc_id,
                FeishuSyncReceipt.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return
    session.add(
        FeishuSyncReceipt(
            source_doc_id=source_doc_id,
            content_hash=content_hash,
            triggered_by=triggered_by,
        )
    )


async def has_sync_receipt(session: AsyncSession, source_doc_id: int, content_hash: str) -> bool:
    row = (
        await session.execute(
            select(FeishuSyncReceipt.source_doc_id).where(
                FeishuSyncReceipt.source_doc_id == source_doc_id,
                FeishuSyncReceipt.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    return row is not None


class FeishuSyncError(Exception):
    """同步流程业务错误（非飞书 API 权限类）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# 不可重试的业务错误（MQ consumer 直接 skip，不走退避）
NON_RETRYABLE_SYNC_ERRORS = frozenset(
    {
        "invalid_feishu_url",
        "invalid_source",
        "not_found",
        "no_sync_state",
        "archived",
        "missing_url",
    }
)


@dataclass
class Phase1Result:
    source_doc_id: int
    import_batch_id: int
    parsed_items: int
    blocking_count: int
    skipped_blocks: int
    total_blocks: int
    markdown: str
    content_hash: str
    ok: bool
    validation_errors: list[dict] = field(default_factory=list)


@dataclass
class Phase2Result:
    source_doc_id: int
    published: int
    pending_review: int
    archived: int
    failed: int
    sync_status: str


@dataclass
class SyncResult:
    phase1: Phase1Result
    phase2: Phase2Result | None = None


def _markdown_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()


def _run_entry_pipeline(type_: str, fields: dict[str, str]) -> tuple[list[dict], bool]:
    """复用 P1 校验 + 敏感检测（与 console/knowledge.run_pipeline 同逻辑）。"""
    findings = [{"rule": f.rule, "level": f.level, "message": f.message} for f in validate(type_, fields)]
    for hit in sensitive.scan("\n".join(fields.values())):
        findings.append(
            {
                "rule": "sensitive",
                "level": "blocking",
                "message": f"命中敏感信息（{hit.rule}）：…{hit.snippet}…，请脱敏后重提",
            }
        )
    blocked = any(f["level"] == "blocking" for f in findings)
    return findings, not blocked


async def _load_feishu_doc(
    session: AsyncSession, source_doc_id: int
) -> tuple[SourceDoc, SyncState]:
    doc = await session.get(SourceDoc, source_doc_id)
    if doc is None:
        raise FeishuSyncError("not_found", "知识文件不存在")
    if doc.source != "feishu":
        raise FeishuSyncError("invalid_source", "非飞书来源文档")
    if doc.status != "active":
        raise FeishuSyncError("archived", "知识文件已归档")
    sync = (
        await session.execute(select(SyncState).where(SyncState.source_doc_id == source_doc_id))
    ).scalar_one_or_none()
    if sync is None:
        raise FeishuSyncError("no_sync_state", "缺少 sync_state 记录")
    return doc, sync


async def _acquire_doc_lock(session: AsyncSession, source_doc_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": source_doc_id},
    )


async def sync_feishu_doc_phase1(
    session: AsyncSession,
    source_doc_id: int,
    *,
    client: FeishuClient,
    oss: OssClient,
    triggered_by: TriggerKind,
    actor_user_id: str | None = None,
) -> Phase1Result:
    """阶段一：拉 Block → 渲染 → 图片转存 → 解析 → 模板校验 → import_batch。"""
    doc, sync = await _load_feishu_doc(session, source_doc_id)
    await _acquire_doc_lock(session, source_doc_id)

    now = datetime.now(UTC)
    sync.sync_status = "syncing"
    sync.last_error = None
    sync.last_error_detail = None
    sync.last_sync_started_at = now
    mirror_feishu_metadata(doc, sync)
    mirror_business_status(doc, sync)
    await session.flush()

    if not sync.feishu_url:
        raise FeishuSyncError("missing_url", "sync_state 缺少 feishu_url")

    try:
        resolved = await resolve_doc(client, sync.feishu_url)
    except FeishuPermissionError as exc:
        apply_permission_failure(doc, sync, exc.platform_code or "feishu_api_error", now=now)
        await session.flush()
        raise
    except FeishuError as exc:
        sync.sync_status = "error"
        sync.last_error = "invalid_feishu_url"
        sync.last_error_detail = {"message": str(exc)}
        mirror_business_status(doc, sync)
        await session.flush()
        raise FeishuSyncError("invalid_feishu_url", str(exc)) from exc

    doc_token = resolved.document_token
    sync.feishu_doc_token = doc_token
    doc.feishu_doc_token = doc_token
    sync.feishu_doc_type = resolved.obj_type
    doc.feishu_doc_type = resolved.obj_type
    if resolved.title:
        doc.source_title = resolved.title
        sync.feishu_title = resolved.title

    perm = await check_permission(client, doc_token)
    if not perm.ok:
        apply_permission_failure(doc, sync, perm.error_code or "feishu_api_error", now=now)
        await session.flush()
        raise FeishuPermissionError(
            perm.error_code or "feishu_api_error",
            perm.error_message or "权限预检失败",
            action_guide=perm.action_guide,
        )

    clear_auth_wait(doc)

    blocks = await client.get_document_blocks(doc_token)
    rendered = blocks_to_markdown(blocks)
    sync.last_block_ids = [b.get("block_id") for b in blocks if b.get("block_id")]
    markdown = await resolve_media_in_markdown(
        rendered.markdown,
        rendered.pending_media,
        client=client,
        oss=oss,
        feishu_doc_token=doc_token,
    )

    batch = ImportBatch(
        domain_code=doc.domain_code,
        type=doc.type,
        file_name=doc.name,
        origin="feishu",
        source_doc_id=doc.id,
        source_url=sync.feishu_url,
        source_title=doc.source_title or doc.name,
        created_by=actor_user_id or sync.registered_by,
        status="previewing",
    )
    session.add(batch)
    await session.flush()

    validation_errors: list[dict] = []
    blocking_count = 0
    items: list[ImportItem] = []

    for seq, entry_md in enumerate(parser.split_entries(markdown), start=1):
        title, fields = parser.parse_sections(entry_md)
        if doc.type == "faq" and fields.get("标准问法", "").strip():
            title = fields["标准问法"].strip()
        validation, is_valid = _run_entry_pipeline(doc.type, fields)
        if not is_valid:
            blocking_count += 1
            validation_errors.extend(validation)
        items.append(
            ImportItem(
                batch_id=batch.id,
                seq=seq,
                title=title,
                content=entry_md,
                validation=validation,
                is_valid=is_valid,
                align_action="new",
            )
        )

    existing_rows = (
        (
            await session.execute(
                select(Knowledge)
                .where(
                    Knowledge.source_doc_id == source_doc_id,
                    Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
                )
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    existing = [
        ExistingEntry(
            kid=r.kid,
            title=r.title,
            content_hash=r.content_hash,
            is_form=r.source_ref.startswith("form:"),
        )
        for r in existing_rows
    ]
    for aligned_item in align(doc.type, markdown, existing):
        if aligned_item.align_action != "disappeared":
            continue
        items.append(
            ImportItem(
                batch_id=batch.id,
                seq=aligned_item.seq,
                title=aligned_item.title,
                content=aligned_item.content,
                validation=[],
                is_valid=True,
                align_action="disappeared",
                match_kid=aligned_item.match_kid,
            )
        )

    session.add_all(items)
    await session.flush()

    content_hash = _markdown_hash(markdown)
    ok = blocking_count == 0
    if not ok:
        sync.sync_status = "error"
        sync.last_error = "validation_blocking"
        mirror_business_status(doc, sync)

    logger.info(
        "feishu phase1 done doc=%s batch=%s items=%d blocking=%d trigger=%s",
        source_doc_id,
        batch.id,
        len(items),
        blocking_count,
        triggered_by,
    )

    return Phase1Result(
        source_doc_id=source_doc_id,
        import_batch_id=batch.id,
        parsed_items=len(items),
        blocking_count=blocking_count,
        skipped_blocks=len(rendered.skipped_blocks),
        total_blocks=len(blocks),
        markdown=markdown,
        content_hash=content_hash,
        ok=ok,
        validation_errors=validation_errors,
    )


async def sync_feishu_doc_phase2(
    session: AsyncSession,
    source_doc_id: int,
    phase1: Phase1Result,
    *,
    viking: VikingClient,
    feishu_client: FeishuClient | None = None,
    actor_user_id: str | None = None,
) -> Phase2Result:
    """阶段二：重拆对齐 → 风险矩阵 → publish / review_task + 飞书卡片。"""
    doc, sync = await _load_feishu_doc(session, source_doc_id)
    await _acquire_doc_lock(session, source_doc_id)

    batch = await session.get(ImportBatch, phase1.import_batch_id)
    if batch is None or batch.source_doc_id != source_doc_id:
        raise FeishuSyncError("batch_not_found", "import_batch 不存在或不匹配")

    existing_rows = (
        (
            await session.execute(
                select(Knowledge)
                .where(
                    Knowledge.source_doc_id == source_doc_id,
                    Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
                )
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    existing = [
        ExistingEntry(
            kid=r.kid,
            title=r.title,
            content_hash=r.content_hash,
            is_form=r.source_ref.startswith("form:"),
        )
        for r in existing_rows
    ]
    aligned = align(doc.type, phase1.markdown, existing)

    risk = score_risk(
        RiskMatrixInput(
            doc_type=doc.type,
            previous_content_hash=sync.content_hash,
            new_content_hash=phase1.content_hash,
            aligned=aligned,
            skipped_blocks=phase1.skipped_blocks,
            total_blocks=phase1.total_blocks,
            blocking_error_count=phase1.blocking_count,
            previous_entry_count=len(existing),
            new_entry_count=len(parser.split_entries(phase1.markdown)),
        )
    )
    publish_mode = publish_mode_for_risk(risk.level)
    risk_note = risk_note_for_score(risk)

    domain = await session.get(Domain, doc.domain_code)
    reviewer_id = domain.reviewer_user_id if domain else None

    by_seq = {i.seq: i for i in aligned}
    items = (
        (
            await session.execute(
                select(ImportItem).where(ImportItem.batch_id == batch.id).order_by(ImportItem.seq)
            )
        )
        .scalars()
        .all()
    )

    published = pending_review = archived = failed = 0
    fail_reasons: dict[str, int] = {}
    failure_items: list[dict] = []
    actor = actor_user_id or sync.registered_by

    logger.info(
        "feishu phase2 start doc=%s batch=%s items=%d risk=%s mode=%s",
        source_doc_id,
        batch.id,
        len(items),
        risk.level,
        publish_mode,
    )

    for item in items:
        aligned_item = by_seq.get(item.seq)
        if aligned_item is None:
            continue
        item.align_action = aligned_item.align_action
        item.match_kid = aligned_item.match_kid
        item.content = aligned_item.content
        item.title = aligned_item.title

        if aligned_item.align_action == "unchanged":
            continue
        if aligned_item.align_action == "disappeared":
            row = await session.get(Knowledge, aligned_item.match_kid)
            if not row or row.status == Status.ARCHIVED:
                continue
            prev_status = row.status
            try:
                row.status = transition(Status(row.status), Event.ARCHIVE)
            except InvalidTransition as exc:
                failed += 1
                _record_phase2_failure(
                    failure_items,
                    fail_reasons,
                    seq=item.seq,
                    reason="disappeared_invalid_transition",
                    title=row.title,
                    align_action="disappeared",
                    extra={"kid": row.kid, "detail": str(exc)},
                )
                _log_phase2_item_failure(
                    source_doc_id,
                    seq=item.seq,
                    reason="disappeared_invalid_transition",
                    align_action="disappeared",
                    kid=row.kid,
                    title=row.title,
                    detail=str(exc),
                )
                continue
            try:
                await viking.delete(build_uri(row.domain_code, row.type, row.kid))
            except Exception as exc:
                logger.exception("viking.delete failed for disappeared kid=%s", row.kid)
                row.status = prev_status
                failed += 1
                _record_phase2_failure(
                    failure_items,
                    fail_reasons,
                    seq=item.seq,
                    reason="disappeared_viking_delete",
                    title=row.title,
                    align_action="disappeared",
                    extra={"kid": row.kid, "detail": str(exc)},
                )
                _log_phase2_item_failure(
                    source_doc_id,
                    seq=item.seq,
                    reason="disappeared_viking_delete",
                    align_action="disappeared",
                    kid=row.kid,
                    title=row.title,
                    detail=str(exc),
                )
                continue
            archived += 1
            continue
        if not item.is_valid:
            failed += 1
            blocking = [v for v in (item.validation or []) if v.get("level") == "blocking"]
            _record_phase2_failure(
                failure_items,
                fail_reasons,
                seq=item.seq,
                reason="validation",
                title=item.title,
                align_action=aligned_item.align_action,
                extra={"validation": blocking[:5]},
            )
            _log_phase2_item_failure(
                source_doc_id,
                seq=item.seq,
                reason="validation",
                align_action=aligned_item.align_action,
                kid=item.match_kid,
                title=item.title,
                detail=str(blocking[:3]) if blocking else "is_valid=false",
            )
            continue

        title, fields = parser.parse_sections(item.content)
        if doc.type == "faq" and fields.get("标准问法"):
            title = fields["标准问法"]
        target_kid = item.match_kid if aligned_item.align_action == "changed" else None
        existing_row = await session.get(Knowledge, target_kid) if target_kid else None
        inp = PublishInput(
            domain_code=doc.domain_code,
            type_=doc.type,
            title=title or "未命名",
            sections=fields,
            tags=existing_row.tags if existing_row else [],
            owner_user_id=existing_row.owner_user_id if existing_row else actor,
            source_type="feishu_doc",
            source_ref=f"feishu:{sync.feishu_doc_token}:{item.seq}",
            source_url=sync.feishu_url,
            effective_date=date.today(),
            expire_date=None,
            actor_user_id=actor,
            source_doc_id=doc.id,
            doc_seq=item.seq,
        )
        try:
            result = await publish(
                session, viking, inp, kid=target_kid, mode=publish_mode
            )
            item.result_kid = result.kid
            if publish_mode == "publish":
                published += 1
            else:
                pending_review += 1
                row = await session.get(Knowledge, result.kid)
                if row:
                    row.risk_note = risk_note[:256]
                pending_exists = (
                    await session.execute(
                        select(ReviewTask.id).where(
                            ReviewTask.kid == result.kid,
                            ReviewTask.status == "pending",
                        )
                    )
                ).scalar_one_or_none()
                if pending_exists is None:
                    task = ReviewTask(
                        kid=result.kid,
                        domain_code=doc.domain_code,
                        task_type="risk",
                        status="pending",
                        risk_note=risk_note[:256],
                        submitter_id=actor,
                        reviewer_id=reviewer_id,
                    )
                    session.add(task)
                    await session.flush()
                    if feishu_client and reviewer_id:
                        msg_id = await send_review_card(
                            feishu_client,
                            receive_id=reviewer_id,
                            kid=result.kid,
                            title=title or "未命名",
                            domain_code=doc.domain_code,
                            doc_title=doc.source_title or doc.name,
                            risk_level=risk.level,
                            risk_note=risk_note,
                            feishu_url=sync.feishu_url,
                        )
                        if msg_id:
                            task.feishu_card_id = msg_id
                            task.card_sent_at = datetime.now(UTC)
        except DuplicateContent as exc:
            failed += 1
            duplicate = await _duplicate_entry_info(session, exc.existing_kid)
            _record_phase2_failure(
                failure_items,
                fail_reasons,
                seq=item.seq,
                reason="duplicate_content",
                title=title,
                align_action=aligned_item.align_action,
                extra={"duplicate": duplicate},
            )
            _log_phase2_item_failure(
                source_doc_id,
                seq=item.seq,
                reason="duplicate_content",
                align_action=aligned_item.align_action,
                kid=target_kid,
                title=title,
                detail=exc.existing_kid,
            )
        except InvalidTransition as exc:
            failed += 1
            _record_phase2_failure(
                failure_items,
                fail_reasons,
                seq=item.seq,
                reason="invalid_transition",
                title=title,
                align_action=aligned_item.align_action,
                extra={"kid": target_kid, "detail": str(exc)},
            )
            _log_phase2_item_failure(
                source_doc_id,
                seq=item.seq,
                reason="invalid_transition",
                align_action=aligned_item.align_action,
                kid=target_kid,
                title=title,
                detail=str(exc),
            )

    batch.status = "confirmed"
    now = datetime.now(UTC)
    sync.content_hash = phase1.content_hash
    sync.last_sync_at = now
    sync.sync_status = "idle" if failed == 0 else "error"
    if failed == 0:
        sync.last_error = None
        sync.last_error_detail = None
        mirror_business_status(doc, sync)
    else:
        message = _phase2_error_message(failed, fail_reasons)
        detail = {
            "code": "phase2_partial_failure",
            "message": message,
            "breakdown": fail_reasons,
            "failures": failure_items,
        }
        _apply_phase2_sync_error(sync, doc, message=message, detail=detail)
    sync.feishu_title = doc.source_title
    doc.updated_at = now

    await session.flush()

    logger.info(
        "feishu phase2 done doc=%s published=%d archived=%d pending_review=%d failed=%d breakdown=%s",
        source_doc_id,
        published,
        archived,
        pending_review,
        failed,
        fail_reasons if failed else {},
    )

    return Phase2Result(
        source_doc_id=source_doc_id,
        published=published,
        pending_review=pending_review,
        archived=archived,
        failed=failed,
        sync_status=sync.sync_status,
    )


async def sync_feishu_doc(
    session: AsyncSession,
    source_doc_id: int,
    *,
    client: FeishuClient,
    oss: OssClient,
    viking: VikingClient,
    triggered_by: TriggerKind,
    actor_user_id: str | None = None,
    run_phase2: bool = True,
) -> SyncResult:
    """完整同步：phase1 +（可选）phase2。"""
    phase1 = await sync_feishu_doc_phase1(
        session,
        source_doc_id,
        client=client,
        oss=oss,
        triggered_by=triggered_by,
        actor_user_id=actor_user_id,
    )
    doc, sync = await _load_feishu_doc(session, source_doc_id)

    if phase1.ok and await has_sync_receipt(session, source_doc_id, phase1.content_hash):
        sync.sync_status = "idle"
        sync.last_sync_at = sync.last_sync_at or datetime.now(UTC)
        sync.last_error = None
        sync.last_error_detail = None
        mirror_business_status(doc, sync)
        await session.flush()
        logger.info("feishu sync skipped duplicate hash doc=%s", source_doc_id)
        return SyncResult(phase1=phase1, phase2=None)

    phase2 = None
    if run_phase2 and phase1.ok:
        phase2 = await sync_feishu_doc_phase2(
            session,
            source_doc_id,
            phase1,
            viking=viking,
            feishu_client=client,
            actor_user_id=actor_user_id,
        )
        await record_sync_receipt(session, source_doc_id, phase1.content_hash, triggered_by)
    elif not phase1.ok:
        sync.sync_status = "error"
        sync.last_error = "validation_blocking"
        mirror_business_status(doc, sync)
        await session.flush()
    elif not run_phase2:
        mirror_business_status(doc, sync)
        await session.flush()
    return SyncResult(phase1=phase1, phase2=phase2)


async def archive_source_doc(session: AsyncSession, source_doc_id: int) -> None:
    """飞书文档删除事件：归档 source_doc（§8.3 / D6）。"""
    doc = await session.get(SourceDoc, source_doc_id)
    if doc is None:
        return
    try:
        now = datetime.now(UTC)
        doc.status = "archived"
        doc.archived_at = now
        doc.sync_status = "archived"
        sync = (
            await session.execute(
                select(SyncState).where(SyncState.source_doc_id == source_doc_id)
            )
        ).scalar_one_or_none()
        if sync:
            sync.sync_status = "archived"
            sync.last_error = None
            sync.last_sync_at = now
            mirror_business_status(doc, sync)
        await session.flush()
        logger.info("feishu source_doc archived id=%s", source_doc_id)
    except Exception:
        await session.rollback()
        logger.exception("archive_source_doc failed id=%s", source_doc_id)
        raise
