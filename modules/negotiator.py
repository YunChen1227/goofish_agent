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
from goofish_agent.goofish_platform.anti_detect import AntiDetect
from goofish_agent.platform.base import PlatformClient


class Negotiator:
    """Phase 5: Price negotiation with diminishing-increment offer strategy."""

    DECAY_FACTOR = 0.5

    def __init__(
        self,
        client: PlatformClient,
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
        logger.info(
            f"Phase 5: 价格谈判 {len(ready)} 个卖家 | "
            f"目标价: ¥{task.target_price} | 最高价: ¥{task.max_price} | "
            f"最大轮数: {task.negotiate_rounds_limit}"
        )
        records: list[NegotiationRecord] = []

        for idx, conv in enumerate(ready, 1):
            candidate = next((c for c in candidates if c.id == conv.candidate_id), None)
            if not candidate:
                continue
            candidate.status = CandidateStatus.NEGOTIATING
            conv.chat_status = ChatStatus.NEGOTIATING
            logger.info(
                f"{'='*60}\n"
                f"[谈判 {idx}/{len(ready)}] 开始谈判: {candidate.title} | "
                f"卖家报价: ¥{candidate.price} | 目标: ¥{task.target_price}"
            )

            record = await self._negotiate(conv, candidate, candidates, task)
            records.append(record)
            self._session.add(record)

            logger.info(
                f"[谈判 {idx}/{len(ready)}] 谈判结果: {record.status.name} | "
                f"共 {record.round_count} 轮"
                + (f" | 成交价: ¥{record.agreed_price}" if record.agreed_price else "")
            )

            if record.status == NegotiationStatus.AGREED:
                candidate.status = CandidateStatus.AGREED

        self._session.commit()
        agreed = sum(1 for r in records if r.status == NegotiationStatus.AGREED)
        logger.info(
            f"{'='*60}\n"
            f"Phase 5 完成: {agreed}/{len(records)} 成功"
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
        platform_name = self._client.platform_display_name
        logger.info(
            f"[谈判] 市场分析数据: "
            f"{platform_name}行情={market.primary_platform or '暂无'} | "
            f"跨平台={market.cross_platform or '暂无'} | "
            f"卖家对比={market.seller_comparison or '暂无'}"
        )

        record = NegotiationRecord(
            id=uuid4(),
            conversation_id=conv.id,
            initial_price=candidate.price,
            target_price=task.target_price,
            status=NegotiationStatus.IN_PROGRESS,
            market_reference={
                "primary_platform": market.primary_platform,
                "cross_platform": market.cross_platform,
                "seller_comparison": market.seller_comparison,
            },
            history=[],
        )

        seller_price = candidate.price

        for round_num in range(1, task.negotiate_rounds_limit + 1):
            record.round_count = round_num
            logger.info(
                f"[谈判 第{round_num}轮] 当前卖家价: ¥{seller_price} | "
                f"目标价: ¥{task.target_price} | 最高可接受: ¥{task.max_price}"
            )

            if seller_price <= task.target_price:
                record.agreed_price = seller_price
                record.status = NegotiationStatus.AGREED
                logger.info(
                    f"[谈判 第{round_num}轮] 卖家价 ¥{seller_price} "
                    f"≤ 目标价 ¥{task.target_price}，直接成交"
                )
                return record

            defects = self._get_defect_descriptions(candidate.id)
            if defects:
                logger.info(f"[谈判 第{round_num}轮] 可用砍价瑕疵: {defects}")

            prompt = build_negotiation_prompt(
                round_num=round_num,
                seller_price=seller_price,
                target_price=task.target_price,
                max_price=task.max_price,
                defects=defects,
                market_data=record.market_reference,
                chat_history=conv.messages,
                platform_name=self._client.platform_display_name,
            )
            message = await self._llm.generate(NEGOTIATION_SYSTEM_PROMPT, prompt)
            logger.info(f"[谈判 第{round_num}轮] LLM生成谈判话术: {message[:200]}")

            if round_num >= 3:
                offer = self._compute_offer(round_num, task.target_price, task.max_price, record)
                record.current_offer = offer
                logger.info(f"[谈判 第{round_num}轮] 计算报价: ¥{offer:.2f}")

            await self._send(conv, candidate.seller_id, message)
            reply = await self._wait_for_reply(conv)

            if not reply:
                record.status = NegotiationStatus.FAILED
                logger.info(f"[谈判 第{round_num}轮] 卖家未回复，谈判失败")
                return record

            logger.info(f"[谈判 第{round_num}轮] 卖家回复: {reply[:200]}")

            counter = self._extract_price(reply)
            if counter:
                record.seller_counter = counter
                logger.info(
                    f"[谈判 第{round_num}轮] 提取到卖家还价: ¥{counter} "
                    f"(变化: ¥{seller_price} → ¥{counter})"
                )
                seller_price = counter
            else:
                logger.info(f"[谈判 第{round_num}轮] 未从回复中提取到具体价格")

            record.history.append({
                "round": round_num,
                "our_message": message,
                "seller_reply": reply,
                "our_offer": record.current_offer,
                "seller_counter": counter,
            })

            if self._is_rejection(reply):
                record.status = NegotiationStatus.REJECTED
                logger.info(f"[谈判 第{round_num}轮] 卖家拒绝交易，谈判终止")
                return record

            if seller_price <= task.max_price and round_num >= task.negotiate_rounds_limit - 1:
                record.agreed_price = seller_price
                record.status = NegotiationStatus.AGREED
                logger.info(
                    f"[谈判 第{round_num}轮] 卖家价 ¥{seller_price} "
                    f"≤ 最高价 ¥{task.max_price} 且已到后期轮次，接受成交"
                )
                return record

        record.status = NegotiationStatus.STALEMATE
        logger.info(f"[谈判] 达到最大轮数 {task.negotiate_rounds_limit}，僵局结束")
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
