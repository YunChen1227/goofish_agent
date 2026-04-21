from __future__ import annotations

import asyncio
import sys
import threading
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlmodel import select

from goofish_agent.config.logging import remove_task_log_file
from goofish_agent.core.state_machine import PHASE_ORDER
from goofish_agent.core.task_manager import TaskManager
from goofish_agent.models.enums import (
    ConditionGrade,
    NotificationChannel,
    PlatformType,
    TaskStatus,
)
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.negotiation import NegotiationRecord
from goofish_agent.models.task import Task
from goofish_agent.schemas.task import TaskCreate, TaskList, TaskResponse
from goofish_agent.storage.database import get_session

router = APIRouter()

_task_managers: dict[PlatformType, TaskManager] = {}


def _run_in_worker_loop(coro_factory) -> None:
    """Run an async task in a dedicated thread with a Proactor event loop.

    Uvicorn uses ``SelectorEventLoop`` on Windows whenever ``reload`` is
    enabled (or when multiple workers are configured), and that loop cannot
    spawn subprocesses.  Playwright requires ``create_subprocess_exec`` to
    launch its driver, so we run the whole task on an independent loop that
    supports subprocesses regardless of what uvicorn picked.
    """

    def _runner() -> None:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_factory())
        except Exception:
            logger.exception("Background task failed")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    threading.Thread(target=_runner, daemon=True).start()


def get_task_manager(platform: PlatformType) -> TaskManager:
    if platform not in _task_managers:
        _task_managers[platform] = TaskManager(platform)
    return _task_managers[platform]


@router.post("/", response_model=TaskResponse)
async def create_task(body: TaskCreate) -> Task:
    with get_session() as session:
        task = Task(
            id=uuid4(),
            user_id=uuid4(),  # TODO: real user auth
            platform=PlatformType(body.platform),
            keywords=body.keywords,
            min_price=body.min_price,
            max_price=body.max_price,
            target_price=body.target_price,
            condition_requirement=ConditionGrade[body.condition_requirement],
            location=body.location,
            exclude_keywords=body.exclude_keywords,
            max_candidates=body.max_candidates,
            max_negotiate_count=body.max_negotiate_count,
            negotiate_rounds_limit=body.negotiate_rounds_limit,
            seller_min_credit=body.seller_min_credit,
            prefer_verified=body.prefer_verified,
            reference_images=body.reference_images,
            image_match_threshold=body.image_match_threshold,
            custom_instructions=body.custom_instructions,
            notification_channel=NotificationChannel[body.notification_channel],
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        task_id = task.id
        platform_type = PlatformType(body.platform)
        _run_in_worker_loop(lambda: _run_task(task_id, platform_type))
        return task


async def _run_task(task_id: UUID, platform: PlatformType) -> None:
    mgr = get_task_manager(platform)
    await mgr.initialize()
    try:
        await mgr.run_task(task_id)
    finally:
        await mgr.shutdown()


@router.get("/", response_model=TaskList)
async def list_tasks() -> TaskList:
    with get_session() as session:
        tasks = session.exec(select(Task)).all()
        return TaskList(tasks=tasks, total=len(tasks))


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID) -> Task:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return task


@router.get("/{task_id}/progress")
async def task_progress(task_id: UUID) -> dict:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return {
            "task_id": str(task.id),
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "current_phase": (
                task.current_phase.value if task.current_phase and hasattr(task.current_phase, "value")
                else (str(task.current_phase) if task.current_phase else None)
            ),
            "phase_order": [p.value for p in PHASE_ORDER],
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "result_summary": task.result_summary,
        }


@router.post("/{task_id}/pause")
async def pause_task(task_id: UUID) -> dict[str, str]:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        mgr = get_task_manager(task.platform)
    await mgr.pause_task(task_id)
    return {"status": "paused"}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: UUID) -> dict[str, str]:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        mgr = get_task_manager(task.platform)
    await mgr.cancel_task(task_id)
    return {"status": "cancelled"}


@router.post("/{task_id}/resume")
async def resume_task(task_id: UUID) -> dict[str, str | None]:
    """Resume a paused or errored task from its last recorded phase.

    Each phase is individually idempotent: Searcher re-uses cached briefs and
    already-persisted candidates, Assessor skips candidates that already have
    an ``AssessmentReport``, and later phases rebuild their state from the DB
    so only the work that was interrupted gets re-executed.
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if task.status not in (TaskStatus.PAUSED, TaskStatus.ERROR):
            raise HTTPException(
                400,
                f"Task status '{task.status.value}' cannot be resumed "
                "(allowed: paused, error)",
            )
        platform_type = task.platform
        from_phase = (
            task.current_phase.value if task.current_phase else None
        )

    _run_in_worker_loop(lambda: _run_task(task_id, platform_type))
    return {"status": "resuming", "from_phase": from_phase}


def _delete_task_cascade(session, task: Task) -> None:
    """Remove task and all related ORM rows (FK order: negotiation → conversation → report → candidate → task)."""
    task_id = task.id
    candidates = list(
        session.exec(
            select(ProductCandidate).where(ProductCandidate.task_id == task_id)
        ).all()
    )
    for cand in candidates:
        convs = list(
            session.exec(
                select(SellerConversation).where(
                    SellerConversation.candidate_id == cand.id
                )
            ).all()
        )
        for conv in convs:
            negs = list(
                session.exec(
                    select(NegotiationRecord).where(
                        NegotiationRecord.conversation_id == conv.id
                    )
                ).all()
            )
            for neg in negs:
                session.delete(neg)
            session.delete(conv)
        reports = list(
            session.exec(
                select(AssessmentReport).where(
                    AssessmentReport.candidate_id == cand.id
                )
            ).all()
        )
        for rep in reports:
            session.delete(rep)
        session.delete(cand)
    session.delete(task)
    session.commit()


@router.delete("/{task_id}")
async def delete_task(task_id: UUID) -> dict[str, str]:
    """Permanently delete a task and its candidates, assessments, chats, negotiations.

    Also deletes the per-task log file so stale logs do not linger in the UI.
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        _delete_task_cascade(session, task)
    remove_task_log_file(task_id)
    return {"status": "deleted"}
