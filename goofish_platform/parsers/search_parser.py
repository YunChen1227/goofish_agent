from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from loguru import logger
from playwright.async_api import Page

if TYPE_CHECKING:
    from goofish_agent.platform.base import PlatformConfig


class PageDiagnosis(Enum):
    HAS_RESULTS = "has_results"
    NO_RESULTS_RECOMMENDATION = "no_results_recommendation"
    NO_RESULTS_EMPTY = "no_results_empty"
    ANTI_BOT = "anti_bot"
    LOGIN_REQUIRED = "login_required"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


NO_RESULT_INDICATORS = [
    "猜你喜欢",
    "没有找到",
    "没有搜索到",
    "暂无相关",
    "暂无搜索结果",
    "未找到相关",
    "没有相关商品",
    "抱歉，没有找到",
    "为你推荐",
    "no results",
]

ANTI_BOT_INDICATORS = [
    "unusual traffic",
    "滑块验证",
    "请完成验证",
    "人机验证",
    "拍脸验证",
    "扫描二维码",
    "安全验证",
    "baxia",
    "slide to verify",
    "please verify",
    "detected unusual",
]

LOGIN_INDICATORS = [
    "请登录",
    "短信登录",
    "密码登录",
    "扫码登录",
    "手机扫码安全登录",
]


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
    pid = qs.get("id", [""])[0] or qs.get("goods_id", [""])[0]
    if pid:
        return pid
    m = re.search(r"/(\d{5,})(?:\.html)?", urlparse(href).path)
    return m.group(1) if m else ""


def _parse_price(text: str) -> float:
    m = re.search(r"[\d.]+", text.replace(",", ""))
    return float(m.group()) if m else 0.0


class SearchParser:
    RESULTS_TIMEOUT_MS = 15_000

    @staticmethod
    async def _diagnose_page(page: Page) -> tuple[PageDiagnosis, str]:
        """Inspect the page body text to determine why no search results appear."""
        try:
            body_text = await page.inner_text("body")
        except Exception:
            body_text = ""

        text_lower = body_text.lower()
        snippet = body_text[:500].replace("\n", " ").strip()

        for indicator in ANTI_BOT_INDICATORS:
            if indicator.lower() in text_lower:
                return PageDiagnosis.ANTI_BOT, snippet

        for indicator in LOGIN_INDICATORS:
            if indicator in body_text:
                return PageDiagnosis.LOGIN_REQUIRED, snippet

        for indicator in NO_RESULT_INDICATORS:
            if indicator in body_text:
                return PageDiagnosis.NO_RESULTS_EMPTY, snippet

        return PageDiagnosis.UNKNOWN, snippet

    @staticmethod
    async def _is_recommendation_section(page: Page) -> bool:
        """Detect if the visible product area is a 'guess you like' fallback
        rather than genuine search results.

        On goofish, when there are no matching results the page silently falls
        back to a '猜你喜欢' recommendation feed.  The recommendation section
        lives in a container whose text / heading includes that phrase.
        """
        selectors = [
            'text="猜你喜欢"',
            '[class*="recommend"] >> text="猜你喜欢"',
            '[class*="guess"] >> text="猜你喜欢"',
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue

        try:
            body_text = await page.inner_text("body")
            if "猜你喜欢" in body_text and "为你推荐" not in body_text:
                heading_el = await page.query_selector(
                    'h2, h3, [class*="title"], [class*="header"], [class*="Title"]'
                )
                if heading_el:
                    heading = await heading_el.inner_text()
                    if "猜你喜欢" in heading:
                        return True
        except Exception:
            pass

        return False

    @staticmethod
    async def parse(page: Page, config: PlatformConfig | None = None) -> list[ProductBrief]:
        link_selector = config.product_link_selector if config else 'a[href*="/item?id="]'

        try:
            await page.wait_for_selector(
                link_selector,
                timeout=SearchParser.RESULTS_TIMEOUT_MS,
            )
        except Exception:
            diagnosis, snippet = await SearchParser._diagnose_page(page)
            if diagnosis == PageDiagnosis.ANTI_BOT:
                logger.error(
                    f"[搜索页诊断] 触发反爬/验证码，无法获取搜索结果\n"
                    f"  页面内容摘要: {snippet}"
                )
            elif diagnosis == PageDiagnosis.LOGIN_REQUIRED:
                logger.error(
                    f"[搜索页诊断] 需要登录，无法获取搜索结果\n"
                    f"  页面内容摘要: {snippet}"
                )
            elif diagnosis == PageDiagnosis.NO_RESULTS_EMPTY:
                logger.info(
                    f"[搜索页诊断] 平台返回无搜索结果\n"
                    f"  页面内容摘要: {snippet}"
                )
            else:
                logger.warning(
                    f"[搜索页诊断] 搜索结果加载超时，未能识别页面状态\n"
                    f"  页面URL: {page.url}\n"
                    f"  页面内容摘要: {snippet}"
                )
            return []

        if await SearchParser._is_recommendation_section(page):
            logger.warning(
                "[搜索页诊断] 页面展示的是「猜你喜欢」推荐内容而非真实搜索结果，"
                "判定为无搜索结果"
            )
            return []

        links = await page.query_selector_all(link_selector)
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

                if config:
                    product_url = config.item_url_template.format(product_id=product_id)
                else:
                    product_url = f"https://www.goofish.com/item?id={product_id}"

                results.append(
                    ProductBrief(
                        product_id=product_id,
                        title=title,
                        price=price,
                        image_url=image_url,
                        seller_name=seller_name,
                        product_url=product_url,
                    )
                )
            except Exception:
                continue

        logger.info(f"解析到 {len(results)} 个有效商品")
        return results
