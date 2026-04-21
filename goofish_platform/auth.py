from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from playwright.async_api import BrowserContext, Page

from goofish_agent.goofish_platform.browser import BrowserEngine
from goofish_agent.utils.crypto import CredentialCrypto

MAX_WAIT_SECONDS = 300
POLL_INTERVAL = 3

_DEFAULT_AUTH_URL_KEYWORDS = ("login", "verify", "auth", "captcha", "security")
_DEFAULT_MODAL_SELECTORS = (
    '[class*="login"][class*="modal"]',
    '[class*="login"][class*="dialog"]',
    '[class*="login"][class*="popup"]',
    '[class*="modal-mask"]',
    '[class*="overlay"][class*="login"]',
    '[class*="baxia"]',
    'iframe[src*="login"]',
)


class AuthManager:
    COOKIE_FILE = "cookies.json"

    def __init__(
        self,
        browser_engine: BrowserEngine,
        crypto: CredentialCrypto | None = None,
        *,
        base_url: str = "https://www.goofish.com/",
        login_button_text: str = "登录",
        auth_url_keywords: tuple[str, ...] | None = None,
        modal_selectors: tuple[str, ...] | None = None,
        cookie_prefix: str = "",
    ) -> None:
        self._browser = browser_engine
        self._crypto = crypto
        self._base_url = base_url
        self._login_button_text = login_button_text
        self._auth_url_keywords = auth_url_keywords or _DEFAULT_AUTH_URL_KEYWORDS
        self._modal_selectors = modal_selectors or _DEFAULT_MODAL_SELECTORS
        self._cookie_file = f"{cookie_prefix}cookies.json" if cookie_prefix else self.COOKIE_FILE

    async def ensure_logged_in(
        self,
        page: Page,
        *,
        user_closed_check: Callable[[], None] | None = None,
    ) -> bool:
        """Navigate to platform base URL and block until logged in."""
        if user_closed_check:
            user_closed_check()
        await page.goto(self._base_url, wait_until="load")
        await asyncio.sleep(3)

        if await self._is_logged_in(page):
            logger.info("已登录且主界面就绪")
            return True
        return await self._wait_until_logged_in(page, user_closed_check=user_closed_check)

    async def _is_logged_in(self, page: Page) -> bool:
        try:
            url = page.url
            if any(kw in url for kw in self._auth_url_keywords):
                return False
            if await self._has_login_button(page):
                return False
            if await self._has_modal(page):
                return False
            return True
        except Exception:
            return False

    async def _has_login_button(self, page: Page) -> bool:
        try:
            loc = page.get_by_text(self._login_button_text, exact=True).first
            return await loc.is_visible(timeout=1000)
        except Exception:
            return False

    async def _has_modal(self, page: Page) -> bool:
        try:
            for sel in self._modal_selectors:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            return False
        except Exception:
            return False

    async def _wait_until_logged_in(
        self,
        page: Page,
        *,
        user_closed_check: Callable[[], None] | None = None,
    ) -> bool:
        """Poll until playwright confirms: logged in + no modal blocking."""
        logger.info("=" * 50)
        logger.info("请在浏览器中完成登录（弹窗关闭后自动继续）")
        logger.info(f"超时时间：{MAX_WAIT_SECONDS // 60} 分钟")
        logger.info("=" * 50)

        import time

        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            if user_closed_check:
                user_closed_check()
            await asyncio.sleep(POLL_INTERVAL)
            if user_closed_check:
                user_closed_check()
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
        Path(self._cookie_file).write_text(data, encoding="utf-8")
        logger.debug("Cookies saved")

    async def load_cookies(self, context: BrowserContext) -> bool:
        path = Path(self._cookie_file)
        if not path.exists():
            return False
        data = path.read_text(encoding="utf-8")
        if self._crypto:
            data = self._crypto.decrypt(data)
        cookies = json.loads(data)
        await context.add_cookies(cookies)
        logger.debug("Cookies loaded")
        return True
