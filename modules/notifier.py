from __future__ import annotations

import httpx
from loguru import logger
from sqlmodel import Session

from goofish_agent.config.settings import get_settings
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.enums import NotificationChannel
from goofish_agent.models.negotiation import NegotiationRecord
from goofish_agent.models.task import Task


class Notifier:
    """Phase 6: Notify user about deal outcomes and task progress."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def notify_deal(
        self, task: Task, candidate: ProductCandidate, negotiation: NegotiationRecord
    ) -> None:
        message = self._build_deal_message(task, candidate, negotiation)
        await self._send(task.notification_channel, message)

    async def notify_failure(self, task: Task, stats: dict) -> None:
        message = self._build_failure_message(task, stats)
        await self._send(task.notification_channel, message)

    async def notify_progress(self, task: Task, phase: str, message: str) -> None:
        await self._send(task.notification_channel, f"[{task.keywords}] {phase}: {message}")

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_deal_message(
        task: Task, candidate: ProductCandidate, negotiation: NegotiationRecord
    ) -> str:
        saved = candidate.price - (negotiation.agreed_price or candidate.price)
        return (
            f"找到合适商品!\n\n"
            f"商品: {candidate.title}\n"
            f"原价: ¥{candidate.price}\n"
            f"成交价: ¥{negotiation.agreed_price}\n"
            f"节省: ¥{saved:.0f}\n"
            f"链接: {candidate.product_url}\n\n"
            f"请尽快确认并下单。"
        )

    @staticmethod
    def _build_failure_message(task: Task, stats: dict) -> str:
        return (
            f"未找到合适商品\n\n"
            f"搜索关键词: {task.keywords}\n"
            f"初筛淘汰: {stats.get('filtered', 0)} 个\n"
            f"品相不合格: {stats.get('assessment_rejected', 0)} 个\n"
            f"沟通失败: {stats.get('chat_failed', 0)} 个\n"
            f"谈判未达成: {stats.get('negotiation_failed', 0)} 个\n\n"
            f"建议: 放宽条件、提高预算或更换关键词。"
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _send(self, channel: NotificationChannel, message: str) -> None:
        logger.info(f"[{channel.value}] {message}")
        if channel == NotificationChannel.EMAIL:
            await self._send_email(message)
        elif channel == NotificationChannel.WEBHOOK:
            await self._send_webhook(message)

    @staticmethod
    async def _send_email(message: str) -> None:
        settings = get_settings()
        if not settings.smtp_host:
            logger.warning("SMTP未配置，跳过邮件通知")
            return
        # TODO: implement via aiosmtplib
        logger.debug("邮件发送占位")

    @staticmethod
    async def _send_webhook(message: str) -> None:
        settings = get_settings()
        if not settings.webhook_url:
            logger.warning("Webhook未配置，跳过通知")
            return
        async with httpx.AsyncClient() as client:
            await client.post(settings.webhook_url, json={"text": message})
