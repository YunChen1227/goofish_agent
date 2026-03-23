from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from playwright.async_api import BrowserContext, Page

from goofish_agent.platform.browser import BrowserEngine
from goofish_agent.utils.crypto import CredentialCrypto


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
        if await self._check_login_status(page):
            return True
        return await self._login(page)

    async def _check_login_status(self, page: Page) -> bool:
        await page.goto("https://www.goofish.com/", wait_until="domcontentloaded")
        # TODO: adapt selector to actual page structure
        try:
            await page.wait_for_selector(
                '[class*="user"], [class*="avatar"]', timeout=5000
            )
            return True
        except Exception:
            return False

    async def _login(self, page: Page) -> bool:
        logger.info("请在浏览器中完成登录（扫码或手动登录）...")
        try:
            await page.wait_for_selector(
                '[class*="user"], [class*="avatar"]', timeout=120_000
            )
            logger.info("登录成功")
            return True
        except Exception:
            logger.error("登录超时")
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
