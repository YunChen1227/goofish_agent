from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlmodel import select

from goofish_agent.core.task_manager import TaskManager
from goofish_agent.models.enums import ConditionGrade, NotificationChannel
from goofish_agent.models.task import Task
from goofish_agent.schemas.task import TaskCreate, TaskList, TaskResponse
from goofish_agent.storage.database import get_session

router = APIRouter()

_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if not _task_manager:
        _task_manager = TaskManager()
    return _task_manager


@router.post("/", response_model=TaskResponse)
async def create_task(body: TaskCreate, background: BackgroundTasks) -> Task:
    with get_session() as session:
        task = Task(
            id=uuid4(),
            user_id=uuid4(),  # TODO: real user auth
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

        background.add_task(_run_task, task.id)
        return task


async def _run_task(task_id: UUID) -> None:
    mgr = get_task_manager()
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


@router.post("/{task_id}/pause")
async def pause_task(task_id: UUID) -> dict[str, str]:
    mgr = get_task_manager()
    await mgr.pause_task(task_id)
    return {"status": "paused"}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: UUID) -> dict[str, str]:
    mgr = get_task_manager()
    await mgr.cancel_task(task_id)
    return {"status": "cancelled"}
