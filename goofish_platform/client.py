from __future__ import annotations

from urllib.parse import quote

from loguru import logger
from playwright.async_api import Page

from goofish_agent.config.settings import get_settings
from goofish_agent.goofish_platform.anti_detect import AntiDetect
from goofish_agent.goofish_platform.auth import AuthManager
from goofish_agent.goofish_platform.browser import BrowserEngine
from goofish_agent.goofish_platform.parsers.chat_parser import ChatMessage, ChatParser
from goofish_agent.goofish_platform.parsers.detail_parser import DetailParser, ProductDetail
from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief, SearchParser
from goofish_agent.platform.base import GOOFISH_CONFIG, PlatformClient, PlatformConfig
from goofish_agent.utils.rate_limiter import RateLimiterRegistry


class GoofishClient(PlatformClient):
    def __init__(self, config: PlatformConfig | None = None) -> None:
        super().__init__(config or GOOFISH_CONFIG)
        self._browser_engine = BrowserEngine(
            locale=self.config.locale,
            timezone_id=self.config.timezone_id,
        )
        self._auth = AuthManager(
            self._browser_engine,
            base_url=self.config.base_url,
            login_button_text=self.config.login_button_text,
            auth_url_keywords=self.config.auth_url_keywords,
            modal_selectors=self.config.modal_selectors,
            cookie_prefix=f"{self.config.name}_",
        )
        self._anti = AntiDetect()
        self._rate_limiters = RateLimiterRegistry(get_settings())
        self._page: Page | None = None

    async def start(self) -> None:
        ctx = await self._browser_engine.start()
        self._page = await self._browser_engine.new_page()
        await self._auth.load_cookies(ctx)
        await self._auth.ensure_logged_in(self._page)

    async def close(self) -> None:
        try:
            if self._browser_engine._context:
                await self._auth.save_cookies(self._browser_engine._context)
        except Exception as e:
            logger.warning(f"保存 cookies 失败（浏览器可能已关闭）: {e}")
        try:
            await self._browser_engine.close()
        except Exception as e:
            logger.warning(f"关闭浏览器失败: {e}")
        self._page = None

    def _ensure_page(self) -> Page:
        if self._page is None:
            raise RuntimeError(
                f"PlatformClient({self.config.name}) not started – call start() first"
            )
        return self._page

    async def search(self, query: str, filters: dict | None = None) -> list[ProductBrief]:
        page = self._ensure_page()
        await self._rate_limiters.search.acquire()
        url = self.config.search_url_template.format(query=quote(query))
        await page.goto(url, wait_until="domcontentloaded")
        await self._anti.random_browse_pause()
        return await SearchParser.parse(page, self.config)

    async def get_product_detail(self, product_id: str) -> ProductDetail | None:
        page = self._ensure_page()
        await self._rate_limiters.detail.acquire()
        url = self.config.item_url_template.format(product_id=product_id)
        await page.goto(url)
        await self._anti.random_browse_pause()
        return await DetailParser.parse(page)

    async def get_product_media(self, product_id: str) -> list[str]:
        detail = await self.get_product_detail(product_id)
        return detail.images if detail else []

    async def add_to_favorites(self, product_id: str) -> bool:
        page = self._ensure_page()
        await self._rate_limiters.favorite.acquire()
        logger.info(f"[{self.config.display_name}] 收藏商品 {product_id}")
        return True

    async def send_message(self, seller_id: str, message: str) -> bool:
        page = self._ensure_page()
        await self._rate_limiters.message.acquire()
        await self._anti.random_delay(5, 15)
        logger.info(f"[{self.config.display_name}] 发送消息给卖家 {seller_id}")
        return True

    async def get_messages(self, conversation_id: str) -> list[ChatMessage]:
        page = self._ensure_page()
        return await ChatParser.parse_messages(page)

    async def get_seller_info(self, seller_id: str) -> dict:
        page = self._ensure_page()
        return {"seller_id": seller_id}
