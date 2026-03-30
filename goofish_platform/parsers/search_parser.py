from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from loguru import logger
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


def _extract_id_from_href(href: str) -> str:
    qs = parse_qs(urlparse(href).query)
    return qs.get("id", [""])[0]


def _parse_price(text: str) -> float:
    m = re.search(r"[\d.]+", text.replace(",", ""))
    return float(m.group()) if m else 0.0


class SearchParser:
    PRODUCT_LINK_SELECTOR = 'a[href*="/item?id="]'
    RESULTS_TIMEOUT_MS = 15_000

    @staticmethod
    async def parse(page: Page) -> list[ProductBrief]:
        try:
            await page.wait_for_selector(
                SearchParser.PRODUCT_LINK_SELECTOR,
                timeout=SearchParser.RESULTS_TIMEOUT_MS,
            )
        except Exception:
            logger.warning("搜索结果加载超时或无结果")
            return []

        links = await page.query_selector_all(SearchParser.PRODUCT_LINK_SELECTOR)
        logger.debug(f"搜索页找到 {len(links)} 个商品链接")

        results: list[ProductBrief] = []
        seen_ids: set[str] = set()

        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                product_id = _extract_id_from_href(href)
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)

                title = ""
                price = 0.0
                image_url = ""
                seller_name = ""

                title_el = await link.query_selector(
                    '[class*="title"], [class*="name"], h3, h2'
                )
                if title_el:
                    title = (await title_el.inner_text()).strip()
                if not title:
                    raw = (await link.inner_text()).strip()
                    title = raw.split("\n")[0][:80]

                price_el = await link.query_selector('[class*="price"]')
                if price_el:
                    price = _parse_price(await price_el.inner_text())

                img_el = await link.query_selector("img")
                if img_el:
                    image_url = (
                        await img_el.get_attribute("src")
                        or await img_el.get_attribute("data-src")
                        or ""
                    )

                seller_el = await link.query_selector(
                    '[class*="seller"], [class*="user"], [class*="nick"]'
                )
                if seller_el:
                    seller_name = (await seller_el.inner_text()).strip()

                results.append(
                    ProductBrief(
                        product_id=product_id,
                        title=title,
                        price=price,
                        image_url=image_url,
                        seller_name=seller_name,
                        product_url=f"https://www.goofish.com/item?id={product_id}",
                    )
                )
            except Exception:
                continue

        logger.info(f"解析到 {len(results)} 个有效商品")
        return results
