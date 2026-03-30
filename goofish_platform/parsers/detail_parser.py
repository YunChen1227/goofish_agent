from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from loguru import logger
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


def _extract_product_id(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    return qs.get("id", [""])[0]


def _parse_price(text: str) -> float:
    m = re.search(r"[\d.]+", text.replace(",", ""))
    return float(m.group()) if m else 0.0


class DetailParser:
    DETAIL_READY_SELECTOR = '[class*="title"], [class*="detail"], [class*="product"]'
    LOAD_TIMEOUT_MS = 15_000

    @staticmethod
    async def parse(page: Page) -> ProductDetail | None:
        try:
            await page.wait_for_selector(
                DetailParser.DETAIL_READY_SELECTOR,
                timeout=DetailParser.LOAD_TIMEOUT_MS,
            )
        except Exception:
            logger.warning(f"商品详情页加载超时: {page.url}")
            return None

        try:
            product_id = _extract_product_id(page.url)

            title = ""
            for sel in ('[class*="title"]', "h1", "h2"):
                el = await page.query_selector(sel)
                if el:
                    title = (await el.inner_text()).strip()
                    if title:
                        break

            description = ""
            for sel in (
                '[class*="desc"]',
                '[class*="content"]',
                '[class*="detail-info"]',
            ):
                el = await page.query_selector(sel)
                if el:
                    description = (await el.inner_text()).strip()
                    if description:
                        break

            price = 0.0
            price_el = await page.query_selector('[class*="price"]')
            if price_el:
                price = _parse_price(await price_el.inner_text())

            images: list[str] = []
            img_els = await page.query_selector_all(
                '[class*="image"] img, [class*="gallery"] img, '
                '[class*="slider"] img, [class*="swiper"] img'
            )
            for img in img_els:
                src = (
                    await img.get_attribute("src")
                    or await img.get_attribute("data-src")
                    or ""
                )
                if src and src not in images:
                    images.append(src)

            video_url: str | None = None
            video_el = await page.query_selector("video source, video")
            if video_el:
                video_url = (
                    await video_el.get_attribute("src")
                    or await video_el.get_attribute("data-src")
                )

            seller_name = ""
            for sel in (
                '[class*="seller"] [class*="name"]',
                '[class*="seller"]',
                '[class*="nick"]',
            ):
                el = await page.query_selector(sel)
                if el:
                    seller_name = (await el.inner_text()).strip()
                    if seller_name:
                        break

            location = ""
            loc_el = await page.query_selector(
                '[class*="location"], [class*="area"], [class*="addr"]'
            )
            if loc_el:
                location = (await loc_el.inner_text()).strip()

            logger.debug(f"解析商品详情: {product_id} - {title[:30]}")
            return ProductDetail(
                product_id=product_id,
                title=title,
                description=description,
                price=price,
                images=images,
                video_url=video_url,
                seller_id="",
                seller_name=seller_name,
                location=location,
                product_url=page.url,
            )
        except Exception as e:
            logger.warning(f"解析商品详情失败: {e}")
            return None
