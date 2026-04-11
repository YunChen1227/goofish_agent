from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from goofish_agent.goofish_platform.parsers.chat_parser import ChatMessage
from goofish_agent.goofish_platform.parsers.detail_parser import ProductDetail
from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief


@dataclass
class PlatformConfig:
    """Platform-specific configuration that drives browser and parser behavior."""

    name: str
    display_name: str
    base_url: str
    search_url_template: str  # e.g. "https://www.goofish.com/search?q={query}"
    item_url_template: str  # e.g. "https://www.goofish.com/item?id={product_id}"
    product_link_selector: str = 'a[href*="/item?id="]'
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    login_button_text: str = "登录"
    auth_url_keywords: tuple[str, ...] = ("login", "verify", "auth", "captcha", "security")
    modal_selectors: tuple[str, ...] = (
        '[class*="login"][class*="modal"]',
        '[class*="login"][class*="dialog"]',
        '[class*="login"][class*="popup"]',
        '[class*="modal-mask"]',
        '[class*="overlay"][class*="login"]',
        '[class*="baxia"]',
        'iframe[src*="login"]',
    )
    extra: dict = field(default_factory=dict)


GOOFISH_CONFIG = PlatformConfig(
    name="goofish",
    display_name="闲鱼",
    base_url="https://www.goofish.com/",
    search_url_template="https://www.goofish.com/search?q={query}",
    item_url_template="https://www.goofish.com/item?id={product_id}",
    product_link_selector='a[href*="/item?id="]',
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)

TAOBAO_CONFIG = PlatformConfig(
    name="taobao",
    display_name="淘宝二手",
    base_url="https://www.taobao.com/",
    search_url_template="https://s.taobao.com/search?q={query}&tab=secondhand",
    item_url_template="https://item.taobao.com/item.htm?id={product_id}",
    product_link_selector='a[href*="item.htm?id="]',
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)

JD_CONFIG = PlatformConfig(
    name="jd",
    display_name="京东二手",
    base_url="https://www.jd.com/",
    search_url_template="https://search.jd.com/Search?keyword={query}&psort=3",
    item_url_template="https://item.jd.com/{product_id}.html",
    product_link_selector='a[href*="item.jd.com/"]',
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)

PDD_CONFIG = PlatformConfig(
    name="pdd",
    display_name="拼多多二手",
    base_url="https://www.pinduoduo.com/",
    search_url_template="https://www.pinduoduo.com/search_result.html?search_key={query}",
    item_url_template="https://www.pinduoduo.com/goods.html?goods_id={product_id}",
    product_link_selector='a[href*="goods.html?goods_id="]',
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
)

PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "goofish": GOOFISH_CONFIG,
    "taobao": TAOBAO_CONFIG,
    "jd": JD_CONFIG,
    "pdd": PDD_CONFIG,
}


class PlatformClient(ABC):
    """Abstract interface for all marketplace platform clients."""

    def __init__(self, config: PlatformConfig) -> None:
        self.config = config

    @property
    def platform_name(self) -> str:
        return self.config.name

    @property
    def platform_display_name(self) -> str:
        return self.config.display_name

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def search(self, query: str, filters: dict | None = None) -> list[ProductBrief]: ...

    @abstractmethod
    async def get_product_detail(self, product_id: str) -> ProductDetail | None: ...

    @abstractmethod
    async def get_product_media(self, product_id: str) -> list[str]: ...

    @abstractmethod
    async def add_to_favorites(self, product_id: str) -> bool: ...

    @abstractmethod
    async def send_message(self, seller_id: str, message: str) -> bool: ...

    @abstractmethod
    async def get_messages(self, conversation_id: str) -> list[ChatMessage]: ...

    @abstractmethod
    async def get_seller_info(self, seller_id: str) -> dict: ...
