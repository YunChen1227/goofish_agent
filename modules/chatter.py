from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.prompts.chat import CHAT_SYSTEM_PROMPT, build_greeting, build_inquiry_prompt
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.enums import CandidateStatus, ChatStatus
from goofish_agent.models.task import Task
from goofish_agent.platform.anti_detect import AntiDetect
from goofish_agent.platform.client import GoofishClient


class Chatter:
    """Phase 4: Seller communication — runs the chat state machine per candidate."""

    CHAT_TIMEOUT = 6 * 3600
    MAX_MESSAGES = 20
    INFO_CHECKLIST = ["使用时长", "出售原因", "隐藏瑕疵", "配件情况", "保修状态", "交易方式"]

    def __init__(self, client: GoofishClient, llm: LLMClient, session: Session) -> None:
        self._client = client
        self._llm = llm
        self._session = session

    async def execute(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[SellerConversation]:
        logger.info(f"Phase 4: 卖家沟通 {len(candidates)} 个候选")
        conversations: list[SellerConversation] = []
        for candidate in candidates:
            conv = await self._chat_with_seller(candidate, task)
            conversations.append(conv)
            self._session.add(conv)
        self._session.commit()
        logger.info(
            f"Phase 4 完成: "
            f"{sum(1 for c in conversations if c.chat_status == ChatStatus.READY)}"
            f"/{len(conversations)} 就绪"
        )
        return conversations

    # ------------------------------------------------------------------
    # State machine
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

        # INIT -> GREETING
        greeting = build_greeting(candidate.title)
        await self._send(candidate.seller_id, greeting, conv)
        conv.chat_status = ChatStatus.GREETING

        # GREETING -> INQUIRY (wait for first reply)
        reply = await self._wait_for_reply(conv)
        if not reply:
            conv.chat_status = ChatStatus.TIMEOUT
            return conv

        conv.chat_status = ChatStatus.INQUIRY

        # INQUIRY loop — collect information
        remaining = list(self.INFO_CHECKLIST)
        if task.custom_instructions:
            remaining.append(task.custom_instructions)

        msg_count = 0
        while remaining and msg_count < self.MAX_MESSAGES:
            prompt = build_inquiry_prompt(remaining, conv.messages)
            message = await self._llm.generate(CHAT_SYSTEM_PROMPT, prompt)
            await self._send(candidate.seller_id, message, conv)
            msg_count += 1

            reply = await self._wait_for_reply(conv)
            if not reply:
                conv.chat_status = ChatStatus.TIMEOUT
                return conv

            collected = await self._analyze_reply(reply, remaining)
            conv.info_collected.update(collected)
            remaining = [item for item in remaining if item not in collected]

        if await self._has_red_flags(conv):
            conv.chat_status = ChatStatus.ABANDONED
            candidate.status = CandidateStatus.REJECTED
            logger.info(f"红旗: 放弃 {candidate.title}")
        else:
            conv.chat_status = ChatStatus.READY

        return conv

    # ------------------------------------------------------------------
    # Messaging helpers
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
    # Analysis helpers
    # ------------------------------------------------------------------

    async def _analyze_reply(self, reply: str, remaining: list[str]) -> dict:
        prompt = (
            f"卖家回复: {reply}\n\n"
            f"需要确认的信息: {', '.join(remaining)}\n\n"
            f"请以JSON格式返回已确认的信息项及其内容: {{\"item\": \"value\"}}"
        )
        result = await self._llm.generate("你是一个信息提取助手。", prompt)
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _has_red_flags(self, conv: SellerConversation) -> bool:
        all_text = " ".join(
            m["content"] for m in conv.messages if m.get("role") == "seller"
        )
        red_flags = ["翻新", "不退不换", "概不负责", "拆机", "进水"]
        return any(flag in all_text for flag in red_flags)
