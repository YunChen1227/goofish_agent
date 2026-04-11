from __future__ import annotations

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from goofish_agent.config.settings import get_settings


class BrowserEngine:
    def __init__(
        self,
        headless: bool | None = None,
        data_dir: str | None = None,
        proxy: str | None = None,
        locale: str = "zh-CN",
        timezone_id: str = "Asia/Shanghai",
    ) -> None:
        s = get_settings()
        self._headless = headless if headless is not None else s.browser_headless
        self._data_dir = data_dir or s.browser_data_dir
        self._proxy = proxy or s.proxy_server
        self._locale = locale
        self._timezone_id = timezone_id
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._data_dir,
            headless=self._headless,
            proxy={"server": self._proxy} if self._proxy else None,
            viewport={"width": 1366, "height": 768},
            locale=self._locale,
            timezone_id=self._timezone_id,
        )
        return self._context

    async def new_page(self) -> Page:
        if not self._context:
            await self.start()
        assert self._context is not None
        return await self._context.new_page()

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None
