from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page


@dataclass
class ProductBrief:
    product_id: str
    title: str
    price: float
    image_url: str
    seller_name: str
    location: str = ""
    product_url: str = ""


class SearchParser:
    # TODO: adapt selectors to actual Goofish page structure
    ITEM_SELECTOR = '[class*="item"], [class*="card"]'

    @staticmethod
    async def parse(page: Page) -> list[ProductBrief]:
        results: list[ProductBrief] = []
        items = await page.query_selector_all(SearchParser.ITEM_SELECTOR)
        for item in items:
            try:
                title = await item.inner_text()
                # TODO: extract real fields from item element
                results.append(
                    ProductBrief(
                        product_id="",
                        title=title,
                        price=0,
                        image_url="",
                        seller_name="",
                    )
                )
            except Exception:
                continue
        return results
