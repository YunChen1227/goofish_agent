from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlatformPrice:
    platform: str
    price: float
    url: str = ""
    note: str = ""


@dataclass
class MarketAnalysis:
    goofish: dict = field(default_factory=dict)
    cross_platform: dict = field(default_factory=dict)
    seller_comparison: dict = field(default_factory=dict)


class MarketAnalyzer:
    def __init__(self, goofish_client: Any = None) -> None:
        self._goofish_client = goofish_client

    async def analyze(
        self,
        keyword: str,
        candidates: list[Any],
        current_seller_price: float,
    ) -> MarketAnalysis:
        goofish_data = await self._analyze_goofish(keyword)
        cross_platform = await self._fetch_cross_platform(keyword)
        seller_comp = self._compare_sellers(candidates, current_seller_price)
        return MarketAnalysis(
            goofish=goofish_data,
            cross_platform=cross_platform,
            seller_comparison=seller_comp,
        )

    async def _analyze_goofish(self, keyword: str) -> dict:
        if not self._goofish_client:
            return {}
        # TODO: call self._goofish_client.search(keyword) and aggregate results
        return {}

    async def _fetch_cross_platform(self, keyword: str) -> dict:
        # TODO: implement real multi-platform scraping via platform adapters
        return {}

    @staticmethod
    def _compare_sellers(candidates: list[Any], current_price: float) -> dict:
        priced = [
            c for c in candidates
            if hasattr(c, "price") and c.price is not None
        ]
        if not priced:
            return {}

        prices = sorted(priced, key=lambda c: c.price)
        all_prices = [c.price for c in prices]

        current_rank = sum(1 for p in all_prices if p < current_price) + 1
        total = len(all_prices)
        percentile = round(current_rank / total, 2) if total else 0.0

        lowest = prices[0]
        highest = prices[-1]

        return {
            "current_seller_rank": current_rank,
            "total_candidates": total,
            "lowest_candidate": {
                "seller": getattr(lowest, "seller_name", ""),
                "price": lowest.price,
                "condition_score": getattr(lowest, "condition_score", 0),
            },
            "highest_candidate": {
                "seller": getattr(highest, "seller_name", ""),
                "price": highest.price,
                "condition_score": getattr(highest, "condition_score", 0),
            },
            "price_percentile": percentile,
            "median_price": round(statistics.median(all_prices), 2),
            "avg_price": round(statistics.mean(all_prices), 2),
        }
