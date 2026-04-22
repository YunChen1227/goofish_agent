from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session, select

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.prompts.chat import build_greeting
from goofish_agent.ai.prompts.todo import (
    TODO_INQUIRY_SYSTEM_PROMPT,
    build_todo_inquiry_prompt,
)
from goofish_agent.ai.todo_agents import (
    apply_updates,
    build_initial_todo_state,
    bump_stuck_count,
    check_todos,
    choose_next_todo,
    plan_todos,
    should_replan,
)
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.enums import CandidateStatus, ChatStatus
from goofish_agent.models.task import Task
from goofish_agent.goofish_platform.anti_detect import AntiDetect
from goofish_agent.platform.base import PlatformClient


class Chatter:
    """Phase 4: 卖家沟通 — 由 TODO list 驱动的对话状态机。

    每个候选的对话生命周期：
        INIT → 打招呼 → (planner 首次规划) → 循环[询问 → 等待 → checker → (lazy replan)]
            → 信息齐全 / 触发红旗 / 超上限 → READY / ABANDONED / TIMEOUT
    """

    CHAT_TIMEOUT = 6 * 3600
    MAX_MESSAGES = 20
    # 用户未填 todo 或选择合并时补充的默认项
    DEFAULT_INFO_CHECKLIST = [
        "使用时长", "出售原因", "隐藏瑕疵", "配件情况", "保修状态", "交易方式"
    ]

    def __init__(self, client: PlatformClient, llm: LLMClient, session: Session) -> None:
        self._client = client
        self._llm = llm
        self._session = session

    async def execute(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[SellerConversation]:
        logger.info(
            f"Phase 4: 卖家沟通 {len(candidates)} 个候选 | "
            f"用户自定义 todo 数: {len(task.buyer_todo_list or [])} | "
            f"默认 checklist: {self.DEFAULT_INFO_CHECKLIST}"
        )
        conversations: list[SellerConversation] = []
        for idx, candidate in enumerate(candidates, 1):
            logger.info(
                f"{'='*60}\n"
                f"[卖家沟通 {idx}/{len(candidates)}] 开始与卖家沟通: "
                f"{candidate.seller_name} | 商品: {candidate.title}"
            )
            conv = await self._chat_with_seller(candidate, task)
            conversations.append(conv)
            self._session.add(conv)
            done_ids = self._get_done_titles(conv)
            logger.info(
                f"[卖家沟通 {idx}/{len(candidates)}] 沟通结果: {conv.chat_status.name} | "
                f"已完成 todo: {done_ids or '无'}"
            )
        self._session.commit()
        ready_count = sum(1 for c in conversations if c.chat_status == ChatStatus.READY)
        logger.info(
            f"{'='*60}\n"
            f"Phase 4 完成: {ready_count}/{len(conversations)} 就绪"
        )
        return conversations

    # ------------------------------------------------------------------
    # 主状态机
    # ------------------------------------------------------------------

    async def _chat_with_seller(
        self, candidate: ProductCandidate, task: Task
    ) -> SellerConversation:
        conv = SellerConversation(
            id=uuid4(),
            candidate_id=candidate.id,
            seller_id=candidate.seller_id,
            chat_status=ChatStatus.INIT,
            messages=[],
            info_collected={},
            started_at=datetime.now(timezone.utc),
        )

        # 1) 初始化 todo_state（用户 todo + 默认合并）
        todo_state = build_initial_todo_state(
            user_todos=task.buyer_todo_list or [],
            default_checklist=self.DEFAULT_INFO_CHECKLIST,
            merge_defaults=True,
        )
        if not todo_state["items"]:
            logger.warning("[卖家沟通] todo list 为空，跳过沟通")
            conv.todo_state = todo_state
            conv.chat_status = ChatStatus.READY  # 无任何问题，直接进入下一阶段
            return conv
        conv.todo_state = todo_state
        logger.info(
            f"[卖家沟通] 初始 TODO 数: {len(todo_state['items'])} | "
            f"items: {[i['title'] for i in todo_state['items']]}"
        )

        # 2) 打招呼 → 等首条回复
        await self._send(candidate.seller_id, build_greeting(candidate.title), conv)
        conv.chat_status = ChatStatus.GREETING

        reply = await self._wait_for_reply(conv)
        if not reply:
            conv.chat_status = ChatStatus.TIMEOUT
            return conv
        conv.chat_status = ChatStatus.INQUIRY

        # 3) 首次 planner（只有用户填了自定义 todo 才调用；纯默认 checklist 也会跑，
        #    以便按"易→难"重排，成本可控）
        await self._run_planner(conv, candidate, task, trigger="initial")

        # 4) 循环：按 plan.next 询问 → checker → 更新 → (lazy replan)
        msg_count = 0
        while msg_count < self.MAX_MESSAGES:
            next_id = choose_next_todo(conv.todo_state)
            if not next_id:
                logger.info("[卖家沟通] 所有 todo 已处理完毕")
                break

            plan = conv.todo_state.get("plan") or {}
            logger.info(
                f"[卖家沟通] 第{msg_count + 1}轮 | 推进 todo={next_id} | "
                f"待办 {self._count_status(conv, 'pending')} 完成 "
                f"{self._count_status(conv, 'done')} 跳过 "
                f"{self._count_status(conv, 'skipped')}"
            )

            user_prompt = build_todo_inquiry_prompt(
                todo_state=conv.todo_state,
                chat_history=conv.messages,
                next_todo_id=next_id,
                plan_reasoning=plan.get("reasoning"),
            )
            message = await self._llm.generate(
                TODO_INQUIRY_SYSTEM_PROMPT, user_prompt, temperature=0.7
            )
            logger.info(f"[卖家沟通] LLM 生成询问: {message[:200]}")
            await self._send(candidate.seller_id, message, conv)
            msg_count += 1

            reply = await self._wait_for_reply(conv)
            if not reply:
                logger.info("[卖家沟通] 等待卖家回复超时")
                conv.chat_status = ChatStatus.TIMEOUT
                return conv
            logger.info(f"[卖家沟通] 卖家回复: {reply[:200]}")

            # ---- Checker ----
            updates = await check_todos(
                self._llm, conv.todo_state, reply, conv.messages
            )
            changed_ids = apply_updates(conv.todo_state, updates)
            # 同步到 info_collected 便于下游（和老逻辑兼容）
            self._sync_info_collected(conv)

            bump_stuck_count(conv.todo_state, next_id)

            # ---- lazy replan ----
            need_replan, reason = should_replan(
                conv.todo_state, next_id, changed_ids, reply
            )
            if need_replan:
                logger.info(f"[卖家沟通] 触发 lazy replan：{reason}")
                await self._run_planner(conv, candidate, task, trigger=reason)

            # 每轮都持久化一次，断点续跑友好
            self._session.add(conv)
            self._session.commit()

        # 5) 收尾：红旗词判断 / READY
        if await self._has_red_flags(conv):
            conv.chat_status = ChatStatus.ABANDONED
            candidate.status = CandidateStatus.REJECTED
            all_seller_text = " ".join(
                m["content"] for m in conv.messages if m.get("role") == "seller"
            )
            hit_flags = [
                f for f in ["翻新", "不退不换", "概不负责", "拆机", "进水"]
                if f in all_seller_text
            ]
            logger.warning(
                f"[卖家沟通] 发现红旗关键词 {hit_flags}，放弃 {candidate.title}"
            )
        else:
            conv.chat_status = ChatStatus.READY
            logger.info(
                f"[卖家沟通] 进入就绪 | 已完成 todo: "
                f"{self._get_done_titles(conv)}"
            )
        return conv

    # ------------------------------------------------------------------
    # Planner 包装
    # ------------------------------------------------------------------

    async def _run_planner(
        self,
        conv: SellerConversation,
        candidate: ProductCandidate,
        task: Task,
        trigger: str,
    ) -> None:
        plan = conv.todo_state.get("plan") or {}
        # 初次规划不计入 replan_count；replan 才计入并受 MAX_REPLAN_TIMES 限制
        is_replan = trigger != "initial"
        if is_replan:
            plan["replan_count"] = int(plan.get("replan_count", 0)) + 1

        assessment_summary = None
        try:
            rep = self._session.exec(
                select(AssessmentReport).where(
                    AssessmentReport.candidate_id == candidate.id
                )
            ).first()
            if rep:
                assessment_summary = (
                    f"{rep.condition_grade.name} 分{rep.condition_score} | "
                    f"瑕疵={[d.get('description','') for d in (rep.defects or [])][:3]} | "
                    f"摘要={rep.summary[:120] if rep.summary else ''}"
                )
        except Exception:
            assessment_summary = None

        await plan_todos(
            self._llm,
            conv.todo_state,
            product_title=candidate.title,
            product_description=candidate.description or "",
            assessment_summary=assessment_summary,
            custom_instructions=task.custom_instructions,
        )
        logger.info(
            f"[卖家沟通] Planner 完成（{trigger}）| "
            f"replan_count={plan.get('replan_count', 0)}/"
        )

    # ------------------------------------------------------------------
    # 状态辅助
    # ------------------------------------------------------------------

    def _count_status(self, conv: SellerConversation, status: str) -> int:
        items = (conv.todo_state or {}).get("items", [])
        return sum(1 for i in items if i.get("status") == status)

    def _get_done_titles(self, conv: SellerConversation) -> list[str]:
        items = (conv.todo_state or {}).get("items", [])
        return [i.get("title") for i in items if i.get("status") == "done"]

    def _sync_info_collected(self, conv: SellerConversation) -> None:
        """保持 info_collected 与 todo_state.items 同步（老字段兼容下游）。"""
        items = (conv.todo_state or {}).get("items", [])
        conv.info_collected = {
            i["title"]: i.get("result") or i.get("evidence") or "已确认"
            for i in items
            if i.get("status") == "done"
        }

    # ------------------------------------------------------------------
    # 消息 IO
    # ------------------------------------------------------------------

    async def _send(self, seller_id: str, text: str, conv: SellerConversation) -> None:
        await AntiDetect.random_delay(15, 60)
        await self._client.send_message(seller_id, text)
        conv.messages.append(
            {"role": "buyer", "content": text, "ts": datetime.now(timezone.utc).isoformat()}
        )
        conv.last_message_at = datetime.now(timezone.utc)

    async def _wait_for_reply(
        self, conv: SellerConversation, timeout: float | None = None
    ) -> str | None:
        timeout = timeout or self.CHAT_TIMEOUT
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            messages = await self._client.get_messages(conv.platform_conversation_id or "")
            seller_msgs = [m for m in messages if m.role == "seller"]
            existing_count = sum(1 for m in conv.messages if m.get("role") == "seller")
            if len(seller_msgs) > existing_count:
                new_msg = seller_msgs[-1]
                conv.messages.append(
                    {"role": "seller", "content": new_msg.content, "ts": new_msg.timestamp}
                )
                return new_msg.content
            await asyncio.sleep(30)
        return None

    # ------------------------------------------------------------------
    # 红旗检查（沿用原逻辑）
    # ------------------------------------------------------------------

    async def _has_red_flags(self, conv: SellerConversation) -> bool:
        all_text = " ".join(
            m["content"] for m in conv.messages if m.get("role") == "seller"
        )
        red_flags = ["翻新", "不退不换", "概不负责", "拆机", "进水"]
        return any(flag in all_text for flag in red_flags)
