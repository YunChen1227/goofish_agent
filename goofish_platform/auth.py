from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger
from playwright.async_api import BrowserContext, Page

from goofish_agent.goofish_platform.browser import BrowserEngine
from goofish_agent.utils.crypto import CredentialCrypto

BASE_URL = "https://www.goofish.com/"
AUTH_URL_KEYWORDS = ("login", "verify", "auth", "captcha", "security")
MAX_WAIT_SECONDS = 300
POLL_INTERVAL = 3


class AuthManager:
    COOKIE_FILE = "cookies.json"

    def __init__(
        self,
        browser_engine: BrowserEngine,
        crypto: CredentialCrypto | None = None,
    ) -> None:
        self._browser = browser_engine
        self._crypto = crypto

    async def ensure_logged_in(self, page: Page) -> bool:
        """Navigate to goofish and block until logged in with no modal open."""
        await page.goto(BASE_URL, wait_until="load")
        await asyncio.sleep(3)

        if await self._is_logged_in(page):
            logger.info("已登录且主界面就绪")
            return True
        return await self._wait_until_logged_in(page)

    async def _is_logged_in(self, page: Page) -> bool:
        """Return True only when:
        1. URL is not an auth redirect
        2. The '登录' button is NOT visible (meaning user is already signed in)
        3. No login modal / popup is blocking the page
        """
        try:
            url = page.url
            if any(kw in url for kw in AUTH_URL_KEYWORDS):
                return False
            if await self._has_login_button(page):
                return False
            if await self._has_modal(page):
                return False
            return True
        except Exception:
            return False

    async def _has_login_button(self, page: Page) -> bool:
        """Check if the header '登录' link is visible (= not logged in)."""
        try:
            loc = page.get_by_text("登录", exact=True).first
            return await loc.is_visible(timeout=1000)
        except Exception:
            return False

    async def _has_modal(self, page: Page) -> bool:
        """Check if any login dialog / modal overlay is currently open."""
        modal_selectors = (
            '[class*="login"][class*="modal"]',
            '[class*="login"][class*="dialog"]',
            '[class*="login"][class*="popup"]',
            '[class*="modal-mask"]',
            '[class*="overlay"][class*="login"]',
            '[class*="baxia"]',
            'iframe[src*="login"]',
        )
        try:
            for sel in modal_selectors:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            return False
        except Exception:
            return False

    async def _wait_until_logged_in(self, page: Page) -> bool:
        """Poll until playwright confirms: logged in + no modal blocking."""
        logger.info("=" * 50)
        logger.info("请在浏览器中完成登录（弹窗关闭后自动继续）")
        logger.info(f"超时时间：{MAX_WAIT_SECONDS // 60} 分钟")
        logger.info("=" * 50)

        import time

        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            if await self._is_logged_in(page):
                logger.info("登录完成，主界面就绪，继续执行任务")
                return True

        logger.error(f"等待超时（{MAX_WAIT_SECONDS // 60} 分钟），未检测到登录状态")
        return False

    async def save_cookies(self, context: BrowserContext) -> None:
        cookies = await context.cookies()
        data = json.dumps(cookies, ensure_ascii=False)
        if self._crypto:
            data = self._crypto.encrypt(data)
        Path(self.COOKIE_FILE).write_text(data, encoding="utf-8")
        logger.debug("Cookies saved")

    async def load_cookies(self, context: BrowserContext) -> bool:
        path = Path(self.COOKIE_FILE)
        if not path.exists():
            return False
        data = path.read_text(encoding="utf-8")
        if self._crypto:
            data = self._crypto.decrypt(data)
        cookies = json.loads(data)
        await context.add_cookies(cookies)
        logger.debug("Cookies loaded")
        return True
