from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.task import Task
from goofish_agent.goofish_platform.parsers.detail_parser import ProductDetail
from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief
from goofish_agent.platform.base import PlatformClient

KEYWORD_OPTIMIZE_PROMPT = (
    "你是一个二手商品搜索优化助手。用户在二手平台搜索商品，但搜索结果与期望不符。\n"
    "请根据用户的原始搜索关键词和搜索结果中的商品标题，分析不匹配的原因，"
    "并给出一个更优的搜索关键词。\n\n"
    "要求：\n"
    "1. 只返回优化后的搜索关键词，不要包含任何解释\n"
    "2. 关键词应该更准确地描述用户想要购买的商品\n"
    "3. 去掉可能导致搜索偏差的冗余词或错别字\n"
    "4. 保留核心商品特征词"
)


class Searcher:
    """Phase 1: Search, filter, optional image matching, and initial scoring."""

    MAX_KEYWORD_OPTIMIZE_RETRIES = 2
    MISMATCH_THRESHOLD = 0.8

    def __init__(
        self, client: PlatformClient, vlm: VLMClient, llm: LLMClient, session: Session
    ) -> None:
        self._client = client
        self._vlm = vlm
        self._llm = llm
        self._session = session

    async def execute(self, task: Task) -> list[ProductCandidate]:
        logger.info(
            f"Phase 1: 搜索 '{task.keywords}' | "
            f"价格区间: ¥{task.min_price}-¥{task.max_price} | "
            f"目标价: ¥{task.target_price} | "
            f"排除关键词: {task.exclude_keywords or '无'}"
        )

        current_keywords = task.keywords
        briefs: list[ProductBrief] = []

        for attempt in range(1 + self.MAX_KEYWORD_OPTIMIZE_RETRIES):
            briefs = await self._client.search(current_keywords, self._build_filters(task))
            logger.info(f"[搜索] 搜索到 {len(briefs)} 个结果")

            need_optimize = False
            optimize_reason = ""

            if not briefs:
                logger.warning(f"[搜索] 关键词 '{current_keywords}' 无搜索结果")
                need_optimize = True
                optimize_reason = "搜索结果为空"
            else:
                mismatch_rate, matched, mismatched = self._check_keyword_relevance(
                    briefs, current_keywords
                )
                logger.info(
                    f"[搜索-预筛选] 关键词相关性检查: "
                    f"匹配 {len(matched)}/{len(briefs)} | "
                    f"不匹配 {len(mismatched)}/{len(briefs)} | "
                    f"不匹配率: {mismatch_rate:.0%}"
                )
                if mismatch_rate >= self.MISMATCH_THRESHOLD:
                    need_optimize = True
                    optimize_reason = (
                        f"搜索结果中 {mismatch_rate:.0%} 的商品标题与关键词不匹配"
                    )

            if not need_optimize:
                break

            if attempt < self.MAX_KEYWORD_OPTIMIZE_RETRIES:
                sample_titles = [b.title for b in briefs[:15] if b.title]
                optimized = await self._optimize_keywords(
                    current_keywords, sample_titles
                )
                if optimized and optimized != current_keywords:
                    mismatched_examples = (
                        [t.title for t in mismatched[:3]] if briefs else []
                    )
                    logger.warning(
                        f"[搜索-关键词优化] {optimize_reason}，"
                        f"触发关键词优化（第 {attempt + 1} 次）\n"
                        f"  原始关键词: '{current_keywords}'\n"
                        f"  优化关键词: '{optimized}'"
                        + (
                            f"\n  不匹配示例: {mismatched_examples}"
                            if mismatched_examples
                            else ""
                        )
                    )
                    current_keywords = optimized
                    delay = random.uniform(5, 10)
                    logger.info(f"[搜索-反爬] 等待 {delay:.1f}s 后使用新关键词重新搜索...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.info("[搜索-关键词优化] 大模型未能给出不同的优化关键词，使用当前结果继续")
                    break
            else:
                logger.info(
                    f"[搜索-关键词优化] 已达最大优化次数 ({self.MAX_KEYWORD_OPTIMIZE_RETRIES})，使用当前结果继续"
                )

        if current_keywords != task.keywords:
            logger.info(f"[搜索] 最终使用关键词: '{current_keywords}'（原始: '{task.keywords}'）")

        logger.info(f"[搜索] 共 {len(briefs)} 个结果，开始逐一检查")

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

    @staticmethod
    def _check_keyword_relevance(
        briefs: list[ProductBrief], keywords: str
    ) -> tuple[float, list[ProductBrief], list[ProductBrief]]:
        tokens = re.split(r'[\s/\\|,，、]+', keywords)
        tokens = [t.lower() for t in tokens if len(t) >= 2]
        if not tokens:
            tokens = [keywords.lower().strip()]

        matched: list[ProductBrief] = []
        mismatched: list[ProductBrief] = []
        for brief in briefs:
            title_lower = brief.title.lower()
            if any(tok in title_lower for tok in tokens):
                matched.append(brief)
            else:
                mismatched.append(brief)

        mismatch_rate = len(mismatched) / len(briefs) if briefs else 0.0
        return mismatch_rate, matched, mismatched

    async def _optimize_keywords(
        self, original_keywords: str, sample_titles: list[str]
    ) -> str:
        titles_text = "\n".join(f"  - {t}" for t in sample_titles)
        user_message = (
            f"原始搜索关键词: {original_keywords}\n\n"
            f"搜索结果中的商品标题（样本）:\n{titles_text}\n\n"
            f"请给出优化后的搜索关键词:"
        )
        try:
            result = await self._llm.generate(
                system_prompt=KEYWORD_OPTIMIZE_PROMPT,
                user_message=user_message,
                temperature=0.3,
            )
            optimized = result.strip().strip('"\'')
            if optimized:
                return optimized
        except Exception as e:
            logger.error(f"[搜索-关键词优化] 调用大模型失败: {e}")
        return original_keywords

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
