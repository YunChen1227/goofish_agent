from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.task import Task
from goofish_agent.goofish_platform.parsers.detail_parser import ProductDetail
from goofish_agent.platform.base import PlatformClient


class Searcher:
    """Phase 1: Search, filter, optional image matching, and initial scoring."""

    def __init__(self, client: PlatformClient, vlm: VLMClient, session: Session) -> None:
        self._client = client
        self._vlm = vlm
        self._session = session

    async def execute(self, task: Task) -> list[ProductCandidate]:
        logger.info(
            f"Phase 1: 搜索 '{task.keywords}' | "
            f"价格区间: ¥{task.min_price}-¥{task.max_price} | "
            f"目标价: ¥{task.target_price} | "
            f"排除关键词: {task.exclude_keywords or '无'}"
        )

        briefs = await self._client.search(task.keywords, self._build_filters(task))
        logger.info(f"[搜索] 搜索到 {len(briefs)} 个结果，开始逐一检查")

        MAX_DETAIL_ATTEMPTS = 10

        candidates: list[ProductCandidate] = []
        attempts = 0
        for brief in briefs[: task.max_candidates]:
            if not brief.product_id:
                continue
            attempts += 1
            detail = await self._client.get_product_detail(brief.product_id)
            if not detail:
                logger.info(f"[搜索 {attempts}/{MAX_DETAIL_ATTEMPTS}] ✗ 详情获取失败: {brief.product_id}")
            elif not self._passes_filters(detail, task):
                logger.info(f"[搜索 {attempts}/{MAX_DETAIL_ATTEMPTS}] ✗ 未通过筛选: {brief.title}")
            else:
                candidates.append(self._to_candidate(detail, task.id))
                logger.info(
                    f"[搜索 {attempts}/{MAX_DETAIL_ATTEMPTS}] ✓ 通过筛选: {detail.title} | "
                    f"¥{detail.price} | 卖家信用: {detail.seller_credit}"
                )
            if attempts >= MAX_DETAIL_ATTEMPTS:
                logger.info(f"已检查 {MAX_DETAIL_ATTEMPTS} 个商品，停止搜索")
                break

        if task.reference_images:
            logger.info(f"[搜索] 开始图片匹配，参考图片: {len(task.reference_images)}张")
            candidates = await self._apply_image_matching(candidates, task)

        candidates = self._score_and_sort(candidates, task)

        if candidates:
            logger.info("[搜索] 初始评分排序结果:")
            for i, c in enumerate(candidates, 1):
                img_info = f" | 图片匹配: {c.image_match_score:.2f}" if c.image_match_score is not None else ""
                logger.info(
                    f"  #{i} {c.title} | ¥{c.price} | "
                    f"综合评分: {c.initial_score:.3f}{img_info}"
                )

        for c in candidates:
            self._session.add(c)
        self._session.commit()

        logger.info(f"Phase 1 完成: {len(candidates)} 个候选商品")
        return candidates

    def _build_filters(self, task: Task) -> dict:
        return {
            "min_price": task.min_price,
            "max_price": task.max_price,
            "location": task.location,
        }

    def _passes_filters(self, detail: ProductDetail, task: Task) -> bool:
        if not (task.min_price <= detail.price <= task.max_price):
            logger.info(
                f"    筛选淘汰原因: 价格 ¥{detail.price} 不在区间 "
                f"¥{task.min_price}-¥{task.max_price}"
            )
            return False
        desc_lower = (detail.title + detail.description).lower()
        hit_keywords = [kw for kw in task.exclude_keywords if kw.lower() in desc_lower]
        if hit_keywords:
            logger.info(f"    筛选淘汰原因: 命中排除关键词 {hit_keywords}")
            return False
        if task.seller_min_credit and detail.seller_credit:
            if detail.seller_credit < task.seller_min_credit:
                logger.info(
                    f"    筛选淘汰原因: 卖家信用 {detail.seller_credit} < "
                    f"最低要求 {task.seller_min_credit}"
                )
                return False
        return True

    async def _apply_image_matching(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[ProductCandidate]:
        matched: list[ProductCandidate] = []
        for i, c in enumerate(candidates, 1):
            if c.images:
                logger.info(f"[图片匹配 {i}/{len(candidates)}] 对比: {c.title}")
                score = await self._vlm.match_images(c.images[:3], task.reference_images)
                c.image_match_score = score
                if score >= task.image_match_threshold:
                    logger.info(
                        f"[图片匹配 {i}/{len(candidates)}] ✓ 匹配度 {score:.2f} "
                        f"≥ 阈值 {task.image_match_threshold} → 保留"
                    )
                    matched.append(c)
                else:
                    logger.info(
                        f"[图片匹配 {i}/{len(candidates)}] ✗ 匹配度 {score:.2f} "
                        f"< 阈值 {task.image_match_threshold} → 淘汰"
                    )
            else:
                logger.info(f"[图片匹配 {i}/{len(candidates)}] 无商品图片，跳过匹配: {c.title}")
                matched.append(c)
        logger.info(f"[图片匹配] 结果: {len(matched)}/{len(candidates)} 通过图片匹配")
        return matched

    def _score_and_sort(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[ProductCandidate]:
        price_range = task.max_price - task.target_price
        for c in candidates:
            price_s = max(0.0, 1 - (c.price - task.target_price) / price_range) if price_range > 0 else 1.0
            credit_s = min((c.seller_credit or 0) / 1000.0, 1.0)
            if c.image_match_score is not None:
                c.initial_score = 0.3 * price_s + 0.2 * credit_s + 0.3 * c.image_match_score + 0.2
            else:
                c.initial_score = 0.4 * price_s + 0.3 * credit_s + 0.3
        candidates.sort(key=lambda x: x.initial_score, reverse=True)
        return candidates

    @staticmethod
    def _to_candidate(detail: ProductDetail, task_id) -> ProductCandidate:
        return ProductCandidate(
            id=uuid4(),
            task_id=task_id,
            platform_product_id=detail.product_id,
            title=detail.title,
            description=detail.description,
            price=detail.price,
            seller_id=detail.seller_id,
            seller_name=detail.seller_name,
            seller_credit=detail.seller_credit,
            images=detail.images,
            video_url=detail.video_url,
            product_url=detail.product_url,
            created_at=datetime.now(timezone.utc),
        )
