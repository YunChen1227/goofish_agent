from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.market_analyzer import MarketAnalyzer
from goofish_agent.ai.prompts.negotiation import NEGOTIATION_SYSTEM_PROMPT, build_negotiation_prompt
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.enums import CandidateStatus, ChatStatus, NegotiationStatus
from goofish_agent.models.negotiation import NegotiationRecord
from goofish_agent.models.task import Task
from goofish_agent.platform.anti_detect import AntiDetect
from goofish_agent.platform.client import GoofishClient


class Negotiator:
    """Phase 5: Price negotiation with diminishing-increment offer strategy."""

    DECAY_FACTOR = 0.5

    def __init__(
        self,
        client: GoofishClient,
        llm: LLMClient,
        market_analyzer: MarketAnalyzer,
        session: Session,
    ) -> None:
        self._client = client
        self._llm = llm
        self._market = market_analyzer
        self._session = session

    async def execute(
        self,
        conversations: list[SellerConversation],
        candidates: list[ProductCandidate],
        task: Task,
    ) -> list[NegotiationRecord]:
        ready = [c for c in conversations if c.chat_status == ChatStatus.READY]
        logger.info(f"Phase 5: 价格谈判 {len(ready)} 个卖家")
        records: list[NegotiationRecord] = []

        for conv in ready:
            candidate = next((c for c in candidates if c.id == conv.candidate_id), None)
            if not candidate:
                continue
            candidate.status = CandidateStatus.NEGOTIATING
            conv.chat_status = ChatStatus.NEGOTIATING

            record = await self._negotiate(conv, candidate, candidates, task)
            records.append(record)
            self._session.add(record)

            if record.status == NegotiationStatus.AGREED:
                candidate.status = CandidateStatus.AGREED
                logger.info(
                    f"达成协议: {candidate.title} ¥{record.agreed_price}"
                )

        self._session.commit()
        logger.info(
            f"Phase 5 完成: "
            f"{sum(1 for r in records if r.status == NegotiationStatus.AGREED)}"
            f"/{len(records)} 成功"
        )
        return records

    # ------------------------------------------------------------------
    # Core negotiation loop
    # ------------------------------------------------------------------

    async def _negotiate(
        self,
        conv: SellerConversation,
        candidate: ProductCandidate,
        all_candidates: list[ProductCandidate],
        task: Task,
    ) -> NegotiationRecord:
        market = await self._market.analyze(task.keywords, all_candidates, candidate.price)

        record = NegotiationRecord(
            id=uuid4(),
            conversation_id=conv.id,
            initial_price=candidate.price,
            target_price=task.target_price,
            status=NegotiationStatus.IN_PROGRESS,
            market_reference={
                "goofish": market.goofish,
                "cross_platform": market.cross_platform,
                "seller_comparison": market.seller_comparison,
            },
            history=[],
        )

        seller_price = candidate.price

        for round_num in range(1, task.negotiate_rounds_limit + 1):
            record.round_count = round_num

            if seller_price <= task.target_price:
                record.agreed_price = seller_price
                record.status = NegotiationStatus.AGREED
                return record

            defects = self._get_defect_descriptions(candidate.id)
            prompt = build_negotiation_prompt(
                round_num=round_num,
                seller_price=seller_price,
                target_price=task.target_price,
                max_price=task.max_price,
                defects=defects,
                market_data=record.market_reference,
                chat_history=conv.messages,
            )
            message = await self._llm.generate(NEGOTIATION_SYSTEM_PROMPT, prompt)

            if round_num >= 3:
                offer = self._compute_offer(round_num, task.target_price, task.max_price, record)
                record.current_offer = offer

            await self._send(conv, candidate.seller_id, message)
            reply = await self._wait_for_reply(conv)

            if not reply:
                record.status = NegotiationStatus.FAILED
                return record

            counter = self._extract_price(reply)
            if counter:
                record.seller_counter = counter
                seller_price = counter

            record.history.append({
                "round": round_num,
                "our_message": message,
                "seller_reply": reply,
                "our_offer": record.current_offer,
                "seller_counter": counter,
            })

            if self._is_rejection(reply):
                record.status = NegotiationStatus.REJECTED
                return record

            if seller_price <= task.max_price and round_num >= task.negotiate_rounds_limit - 1:
                record.agreed_price = seller_price
                record.status = NegotiationStatus.AGREED
                return record

        record.status = NegotiationStatus.STALEMATE
        return record

    # ------------------------------------------------------------------
    # Offer computation
    # ------------------------------------------------------------------

    def _compute_offer(
        self, round_num: int, target: float, max_price: float, record: NegotiationRecord
    ) -> float:
        base = target * 0.9
        if round_num == 3:
            return base
        prev = record.current_offer or base
        increment = (max_price - target) * (self.DECAY_FACTOR ** (round_num - 3))
        return min(prev + increment, max_price)

    # ------------------------------------------------------------------
    # Reply parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(text: str) -> float | None:
        matches = re.findall(r"(\d+(?:\.\d+)?)\s*[元块]?", text)
        prices = [float(m) for m in matches if 10 < float(m) < 100000]
        return max(prices) if prices else None

    @staticmethod
    def _is_rejection(text: str) -> bool:
        signals = ["不卖了", "不出了", "已出", "卖掉了", "不议价", "一口价"]
        return any(s in text for s in signals)

    def _get_defect_descriptions(self, candidate_id) -> list[str]:
        report = (
            self._session.query(AssessmentReport)
            .filter_by(candidate_id=candidate_id)
            .first()
        )
        if report and report.defects:
            return [d.get("description", "") for d in report.defects]
        return []

    # ------------------------------------------------------------------
    # Messaging helpers
    # ------------------------------------------------------------------

    async def _send(
        self, conv: SellerConversation, seller_id: str, text: str
    ) -> None:
        await AntiDetect.random_delay(5, 15)
        await self._client.send_message(seller_id, text)
        conv.messages.append(
            {"role": "buyer", "content": text, "ts": datetime.now(timezone.utc).isoformat()}
        )

    async def _wait_for_reply(
        self, conv: SellerConversation, timeout: float = 3600
    ) -> str | None:
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            messages = await self._client.get_messages(conv.platform_conversation_id or "")
            seller_msgs = [m for m in messages if m.role == "seller"]
            existing = sum(1 for m in conv.messages if m.get("role") == "seller")
            if len(seller_msgs) > existing:
                new_msg = seller_msgs[-1]
                conv.messages.append(
                    {"role": "seller", "content": new_msg.content, "ts": new_msg.timestamp}
                )
                return new_msg.content
            await asyncio.sleep(30)
        return None
