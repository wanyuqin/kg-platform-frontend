"""控制台审核待办 API（P2，review_task 表）。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.console import auth
from app.console.router_deps import current_user
from app.domain.state_machine import Event, InvalidTransition, Status, transition
from app.pipeline.publish import build_frontmatter
from app.storage.pg.models import ConsoleUser, Domain, DomainMember, Knowledge, KnowledgeVersion, ReviewTask
from app.storage.pg.session import get_session
from app.storage.viking.client import VikingClient, VikingError, build_uri, get_viking

router = APIRouter()


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


def _task_out(task: ReviewTask, knowledge: Knowledge | None, submitter: ConsoleUser | None) -> dict:
    k_out = None
    if knowledge:
        k_out = {
            "title": knowledge.title,
            "status": knowledge.status,
            "type": knowledge.type,
            "risk_note": knowledge.risk_note,
        }
    return {
        "id": task.id,
        "kid": task.kid,
        "domain": task.domain_code,
        "task_type": task.task_type,
        "status": task.status,
        "risk_note": task.risk_note,
        "submitter_id": task.submitter_id,
        "submitter_name": submitter.name if submitter else None,
        "reviewer_id": task.reviewer_id,
        "reject_reason": task.reject_reason,
        "created_at": task.created_at.isoformat(),
        "knowledge": k_out,
    }


async def _load_task(session: AsyncSession, user: ConsoleUser, task_id: int) -> ReviewTask:
    task = await session.get(ReviewTask, task_id)
    if task is None:
        raise errors.not_found()
    try:
        await auth.require_domain_role(session, user, task.domain_code, {"admin", "member"})
    except errors.ApiError as exc:
        if exc.code == "forbidden":
            raise errors.not_found() from exc
        raise
    return task


async def _can_resolve(session: AsyncSession, user: ConsoleUser, task: ReviewTask) -> None:
    """approve/reject：domain 管理员或指定 reviewer（7.2）。"""
    if user.is_platform_admin:
        return
    member = (
        await session.execute(
            select(DomainMember).where(
                DomainMember.domain_code == task.domain_code,
                DomainMember.user_id == user.user_id,
            )
        )
    ).scalar_one_or_none()
    if member and member.role == "admin":
        return
    domain = await session.get(Domain, task.domain_code)
    if domain and domain.reviewer_user_id == user.user_id:
        return
    raise errors.forbidden()


async def _write_viking_if_needed(
    session: AsyncSession, viking: VikingClient, row: Knowledge
) -> str:
    if row.index_state != "none":
        return row.index_state
    ver = (
        await session.execute(
            select(KnowledgeVersion).where(
                KnowledgeVersion.kid == row.kid,
                KnowledgeVersion.version == row.version,
            )
        )
    ).scalar_one_or_none()
    if ver is None:
        return row.index_state
    try:
        await viking.write(
            build_uri(row.domain_code, row.type, row.kid),
            build_frontmatter(
                row.kid, row.title, row.domain_code, row.type, row.tags, row.source_url
            )
            + ver.content,
        )
        row.index_state = "indexing"
    except VikingError:
        row.index_state = "failed"
    return row.index_state


@router.get("/review-tasks")
async def list_review_tasks(
    domain: str | None = None,
    task_type: str | None = None,
    status: str = "pending",
    page: int = 1,
    page_size: int = 20,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    page_size = min(max(page_size, 1), 100)
    stmt = select(ReviewTask).order_by(ReviewTask.created_at.desc())
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(ReviewTask.domain_code.in_(member_domains))
    if domain:
        await auth.require_domain_role(session, user, domain, {"admin", "member"})
        stmt = stmt.where(ReviewTask.domain_code == domain)
    if task_type:
        stmt = stmt.where(ReviewTask.task_type == task_type)
    if status:
        stmt = stmt.where(ReviewTask.status == status)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    tasks = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    kid_set = {t.kid for t in tasks}
    knowledge_map = {}
    if kid_set:
        rows = (
            await session.execute(select(Knowledge).where(Knowledge.kid.in_(kid_set)))
        ).scalars().all()
        knowledge_map = {r.kid: r for r in rows}
    submitter_ids = {t.submitter_id for t in tasks}
    submitter_map = {}
    if submitter_ids:
        users = (
            await session.execute(
                select(ConsoleUser).where(ConsoleUser.user_id.in_(submitter_ids))
            )
        ).scalars().all()
        submitter_map = {u.user_id: u for u in users}
    return {
        "items": [
            _task_out(t, knowledge_map.get(t.kid), submitter_map.get(t.submitter_id))
            for t in tasks
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/review-tasks/{task_id}")
async def review_task_detail(
    task_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    task = await _load_task(session, user, task_id)
    knowledge = await session.get(Knowledge, task.kid)
    submitter = await session.get(ConsoleUser, task.submitter_id)
    return _task_out(task, knowledge, submitter)


@router.post("/review-tasks/{task_id}/approve")
async def approve_review_task(
    task_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    task = await _load_task(session, user, task_id)
    await _can_resolve(session, user, task)
    if task.status != "pending":
        raise errors.conflict("任务已处理")
    row = await session.get(Knowledge, task.kid)
    if row is None:
        raise errors.not_found()
    if row.status != Status.PENDING_REVIEW:
        raise errors.conflict("知识不在待审核状态")
    try:
        row.status = transition(Status(row.status), Event.REVIEW_APPROVE)
    except InvalidTransition as exc:
        raise errors.conflict(str(exc)) from exc
    row.risk_note = None
    index_state = await _write_viking_if_needed(session, viking, row)
    task.status = "approved"
    task.resolved_by = user.user_id
    task.resolved_at = datetime.now(UTC)
    await session.commit()
    return {
        "id": task.id,
        "kid": row.kid,
        "status": row.status,
        "index_state": index_state,
        "task_status": task.status,
    }


@router.post("/review-tasks/{task_id}/reject")
async def reject_review_task(
    task_id: int,
    body: RejectBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    task = await _load_task(session, user, task_id)
    await _can_resolve(session, user, task)
    if task.status != "pending":
        raise errors.conflict("任务已处理")
    row = await session.get(Knowledge, task.kid)
    if row is None:
        raise errors.not_found()
    if row.status != Status.PENDING_REVIEW:
        raise errors.conflict("知识不在待审核状态")
    try:
        row.status = transition(Status(row.status), Event.REVIEW_REJECT)
    except InvalidTransition as exc:
        raise errors.conflict(str(exc)) from exc
    row.risk_note = None
    task.status = "rejected"
    task.reject_reason = body.reason.strip()
    task.resolved_by = user.user_id
    task.resolved_at = datetime.now(UTC)
    await session.commit()
    return {
        "id": task.id,
        "kid": row.kid,
        "status": row.status,
        "task_status": task.status,
        "reject_reason": task.reject_reason,
    }
