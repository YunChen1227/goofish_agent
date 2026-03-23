from __future__ import annotations

from uuid import UUID

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.market_analyzer import MarketAnalyzer
from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.core.state_machine import StateMachine
from goofish_agent.models.enums import (
    CandidateStatus,
    ChatStatus,
    NegotiationStatus,
    TaskStatus,
)
from goofish_agent.models.task import Task
from goofish_agent.modules.assessor import Assessor
from goofish_agent.modules.chatter import Chatter
from goofish_agent.modules.favoriter import Favoriter
from goofish_agent.modules.negotiator import Negotiator
from goofish_agent.modules.notifier import Notifier
from goofish_agent.modules.searcher import Searcher
from goofish_agent.platform.client import GoofishClient
from goofish_agent.storage.database import get_session
from goofish_agent.storage.media_store import MediaStore


class TaskManager:
    def __init__(self) -> None:
        self._client: GoofishClient | None = None
        self._vlm: VLMClient | None = None
        self._llm: LLMClient | None = None
        self._market: MarketAnalyzer | None = None
        self._media: MediaStore | None = None

    async def initialize(self) -> None:
        self._client = GoofishClient()
        self._vlm = VLMClient()
        self._llm = LLMClient()
        self._market = MarketAnalyzer(self._client)
        self._media = MediaStore()
        await self._client.start()

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()

    # ------------------------------------------------------------------
    # Public task lifecycle
    # ------------------------------------------------------------------

    async def run_task(self, task_id: UUID) -> None:
        """Execute a complete buying task through all phases."""
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            StateMachine.transition(task, TaskStatus.RUNNING)
            session.commit()

            try:
                await self._execute_phases(task, session)
            except Exception as e:
                logger.error(f"Task {task_id} error: {e}")
                if StateMachine.can_transition(task.status, TaskStatus.ERROR):
                    StateMachine.transition(task, TaskStatus.ERROR)
                    session.commit()
                raise

    async def pause_task(self, task_id: UUID) -> None:
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            StateMachine.transition(task, TaskStatus.PAUSED)
            session.commit()

    async def resume_task(self, task_id: UUID) -> None:
        """Resume from last phase (simplified: re-runs from start)."""
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            StateMachine.transition(task, TaskStatus.RUNNING)
            task.current_phase = None
            session.commit()
        await self.run_task(task_id)

    async def cancel_task(self, task_id: UUID) -> None:
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            StateMachine.transition(task, TaskStatus.CANCELLED)
            session.commit()

    # ------------------------------------------------------------------
    # Internal phase execution
    # ------------------------------------------------------------------

    async def _execute_phases(self, task: Task, session: Session) -> None:
        assert self._client and self._vlm and self._llm and self._market and self._media
        notifier = Notifier(session)

        # Phase 1: Search
        StateMachine.advance_phase(task)
        session.commit()
        await notifier.notify_progress(task, "搜索", "开始搜索商品...")

        searcher = Searcher(self._client, self._vlm, session)
        candidates = await searcher.execute(task)
        if not candidates:
            StateMachine.transition(task, TaskStatus.FAILED)
            await notifier.notify_failure(task, {"filtered": 0})
            session.commit()
            return

        # Phase 2: Assessment
        StateMachine.advance_phase(task)
        session.commit()
        await notifier.notify_progress(task, "品相鉴定", f"评估 {len(candidates)} 个商品...")

        assessor = Assessor(self._vlm, self._media, session)
        reports = await assessor.execute(candidates, task)
        passed = [c for c in candidates if c.status != CandidateStatus.REJECTED]
        if not passed:
            StateMachine.transition(task, TaskStatus.FAILED)
            await notifier.notify_failure(task, {"assessment_rejected": len(candidates)})
            session.commit()
            return

        # Phase 3: Favorite & Rank
        StateMachine.advance_phase(task)
        session.commit()

        favoriter = Favoriter(self._client, session)
        top_candidates = await favoriter.execute(candidates, reports, task)
        await notifier.notify_progress(
            task, "收藏排序", f"{len(top_candidates)} 个商品进入沟通"
        )

        # Phase 4: Chat
        StateMachine.advance_phase(task)
        session.commit()

        chatter = Chatter(self._client, self._llm, session)
        conversations = await chatter.execute(top_candidates, task)
        ready_convs = [c for c in conversations if c.chat_status == ChatStatus.READY]
        if not ready_convs:
            StateMachine.transition(task, TaskStatus.FAILED)
            await notifier.notify_failure(
                task, {"chat_failed": len(conversations) - len(ready_convs)}
            )
            session.commit()
            return

        # Phase 5: Negotiate
        StateMachine.advance_phase(task)
        session.commit()

        negotiator = Negotiator(self._client, self._llm, self._market, session)
        records = await negotiator.execute(conversations, top_candidates, task)
        agreed = [r for r in records if r.status == NegotiationStatus.AGREED]

        # Phase 6: Notify
        StateMachine.advance_phase(task)
        session.commit()

        if agreed:
            for record in agreed:
                conv = next(
                    (c for c in conversations if c.id == record.conversation_id), None
                )
                candidate = next(
                    (c for c in top_candidates if conv and c.id == conv.candidate_id),
                    None,
                )
                if candidate:
                    await notifier.notify_deal(task, candidate, record)
            StateMachine.transition(task, TaskStatus.COMPLETED)
        else:
            await notifier.notify_failure(
                task, {"negotiation_failed": len(records) - len(agreed)}
            )
            StateMachine.transition(task, TaskStatus.FAILED)

        session.commit()
