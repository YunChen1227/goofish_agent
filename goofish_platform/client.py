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
from goofish_agent.utils.rate_limiter import RateLimiterRegistry


class GoofishClient:
    def __init__(self) -> None:
        self._browser_engine = BrowserEngine()
        self._auth = AuthManager(self._browser_engine)
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
            raise RuntimeError("GoofishClient not started – call start() first")
        return self._page

    async def search(self, query: str, filters: dict | None = None) -> list[ProductBrief]:
        page = self._ensure_page()
        await self._rate_limiters.search.acquire()
        url = f"https://www.goofish.com/search?q={quote(query)}"
        await page.goto(url, wait_until="domcontentloaded")
        await self._anti.random_browse_pause()
        return await SearchParser.parse(page)

    async def get_product_detail(self, product_id: str) -> ProductDetail | None:
        page = self._ensure_page()
        await self._rate_limiters.detail.acquire()
        await page.goto(f"https://www.goofish.com/item?id={product_id}")
        await self._anti.random_browse_pause()
        return await DetailParser.parse(page)

    async def get_product_media(self, product_id: str) -> list[str]:
        detail = await self.get_product_detail(product_id)
        return detail.images if detail else []

    async def add_to_favorites(self, product_id: str) -> bool:
        page = self._ensure_page()
        await self._rate_limiters.favorite.acquire()
        # TODO: navigate to product and click favorite button
        logger.info(f"收藏商品 {product_id}")
        return True

    async def send_message(self, seller_id: str, message: str) -> bool:
        page = self._ensure_page()
        await self._rate_limiters.message.acquire()
        await self._anti.random_delay(5, 15)
        # TODO: navigate to chat and send message
        logger.info(f"发送消息给卖家 {seller_id}")
        return True

    async def get_messages(self, conversation_id: str) -> list[ChatMessage]:
        page = self._ensure_page()
        # TODO: navigate to conversation page
        return await ChatParser.parse_messages(page)

    async def get_seller_info(self, seller_id: str) -> dict:
        page = self._ensure_page()
        # TODO: navigate to seller profile and parse
        return {"seller_id": seller_id}
