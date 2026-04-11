from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page


@dataclass
class ChatMessage:
    role: str  # "buyer" | "seller"
    content: str
    timestamp: str = ""


class ChatParser:
    # TODO: adapt selectors to actual platform page structure

    @staticmethod
    async def parse_messages(page: Page) -> list[ChatMessage]:
        return []

    @staticmethod
    async def send_message(page: Page, text: str) -> bool:
        try:
            # TODO: fill textarea and click send
            return True
        except Exception:
            return False
