from __future__ import annotations

from dataclasses import dataclass, field

from playwright.async_api import Page


@dataclass
class ProductDetail:
    product_id: str
    title: str
    description: str
    price: float
    images: list[str] = field(default_factory=list)
    video_url: str | None = None
    seller_id: str = ""
    seller_name: str = ""
    seller_credit: int | None = None
    location: str = ""
    product_url: str = ""


class DetailParser:
    # TODO: adapt selectors to actual Goofish page structure
    TITLE_SELECTOR = '[class*="title"]'

    @staticmethod
    async def parse(page: Page) -> ProductDetail | None:
        try:
            title = await page.inner_text(DetailParser.TITLE_SELECTOR)
            # TODO: extract remaining fields
            return ProductDetail(
                product_id=page.url.split("/")[-1],
                title=title,
                description="",
                price=0,
                product_url=page.url,
            )
        except Exception:
            return None
