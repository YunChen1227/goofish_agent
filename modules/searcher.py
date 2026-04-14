from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.keyword_optimizer import KeywordOptimizer, OptimizationResult
from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.task import Task
from goofish_agent.goofish_platform.parsers.detail_parser import ProductDetail
from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief
from goofish_agent.platform.base import PlatformClient


class Searcher:
    """Phase 1: Search, filter, optional image matching, and initial scoring."""

    MAX_KEYWORD_OPTIMIZE_RETRIES = 3
    MISMATCH_THRESHOLD = 0.8

    def __init__(
        self, client: PlatformClient, vlm: VLMClient, llm: LLMClient, session: Session
    ) -> None:
        self._client = client
        self._vlm = vlm
        self._llm = llm
        self._optimizer = KeywordOptimizer(llm)
        self._session = session

    async def execute(self, task: Task) -> list[ProductCandidate]:
        logger.info(
            f"Phase 1: 搜索 '{task.keywords}' | "
            f"价格区间: ¥{task.min_price}-¥{task.max_price} | "
            f"目标价: ¥{task.target_price} | "
            f"排除关键词: {task.exclude_keywords or '无'}"
        )

        briefs = await self._smart_search(task)

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

    async def _smart_search(self, task: Task) -> list[ProductBrief]:
        """Search with agent-powered keyword optimization and up to
        MAX_KEYWORD_OPTIMIZE_RETRIES retry rounds."""
        current_keywords = task.keywords
        optimization_history: list[OptimizationResult] = []
        briefs: list[ProductBrief] = []

        for attempt in range(1 + self.MAX_KEYWORD_OPTIMIZE_RETRIES):
            briefs = await self._client.search(
                current_keywords, self._build_filters(task)
            )
            logger.info(f"[搜索] 搜索到 {len(briefs)} 个结果 (关键词: '{current_keywords}')")

            if not briefs:
                logger.warning(f"[搜索] 关键词 '{current_keywords}' 无搜索结果")
            else:
                mismatch_rate, matched, mismatched = await self._check_relevance_llm(
                    briefs, task.keywords
                )
                logger.info(
                    f"[搜索-预筛选] 大模型相关性判定: "
                    f"相关 {len(matched)}/{len(briefs)} | "
                    f"不相关 {len(mismatched)}/{len(briefs)} | "
                    f"不相关率: {mismatch_rate:.0%}"
                )
                if mismatch_rate < self.MISMATCH_THRESHOLD:
                    break

            if attempt >= self.MAX_KEYWORD_OPTIMIZE_RETRIES:
                logger.info(
                    f"[搜索-关键词优化] 已达最大优化次数 "
                    f"({self.MAX_KEYWORD_OPTIMIZE_RETRIES})，使用当前结果继续"
                )
                break

            sample_titles = [b.title for b in briefs[:15] if b.title]
            opt_result = await self._optimizer.optimize(
                original_keywords=task.keywords,
                sample_titles=sample_titles or None,
                search_history=optimization_history or None,
            )
            optimization_history.append(opt_result)

            if (
                opt_result.optimized_keywords
                and opt_result.optimized_keywords != current_keywords
            ):
                logger.warning(
                    f"[搜索-关键词优化] 触发关键词优化（第 {attempt + 1}/{self.MAX_KEYWORD_OPTIMIZE_RETRIES} 次）\n"
                    f"  '{current_keywords}' → '{opt_result.optimized_keywords}'"
                )
                current_keywords = opt_result.optimized_keywords
                delay = random.uniform(5, 10)
                logger.info(
                    f"[搜索-反爬] 等待 {delay:.1f}s 后使用新关键词重新搜索..."
                )
                await asyncio.sleep(delay)
            else:
                logger.info(
                    "[搜索-关键词优化] Agent 未能给出不同的优化关键词，使用当前结果继续"
                )
                break

        if current_keywords != task.keywords:
            logger.info(
                f"[搜索] 最终使用关键词: '{current_keywords}'（原始: '{task.keywords}'）"
            )

        return briefs

    async def _check_relevance_llm(
        self,
        briefs: list[ProductBrief],
        user_keywords: str,
    ) -> tuple[float, list[ProductBrief], list[ProductBrief]]:
        """Use LLM to judge relevance of each product title against the user's
        original search intent.  Returns (mismatch_rate, matched, mismatched)."""
        sample = briefs[:20]
        titles_block = "\n".join(
            f"  {i}. {b.title}" for i, b in enumerate(sample, 1)
        )

        system_prompt = (
            "你是一个商品搜索相关性判定助手。用户想购买某个商品，给你一批搜索结果的标题。\n"
            "请判断每个标题是否与用户的搜索意图相关。\n\n"
            "规则：\n"
            "1. 理解用户搜索关键词背后的真实购买意图（可能包含缩写、行话、错别字）\n"
            "2. 标题不需要完全包含关键词，只要商品本身与用户想买的东西相关即可判定为「相关」\n"
            "3. 完全不同品类的商品判定为「不相关」\n\n"
            "请严格按以下 JSON 格式输出，不要输出任何其他内容：\n"
            '{"intent": "一句话描述用户想买什么", '
            '"results": [{"id": 1, "relevant": true/false, "reason": "简短理由"}, ...]}'
        )

        user_message = (
            f"用户搜索关键词: {user_keywords}\n\n"
            f"搜索结果标题列表:\n{titles_block}"
        )

        try:
            raw = await self._llm.generate(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
            )
            relevant_ids = self._parse_relevance_response(raw, len(sample))
        except Exception as e:
            logger.warning(f"[搜索-预筛选] 大模型相关性判定失败 ({e})，回退到全部保留")
            return 0.0, list(briefs), []

        matched: list[ProductBrief] = []
        mismatched: list[ProductBrief] = []
        for i, brief in enumerate(sample):
            is_relevant = relevant_ids.get(i + 1, True)
            if is_relevant:
                matched.append(brief)
            else:
                mismatched.append(brief)
                logger.debug(f"  [不相关] {brief.title}")

        remaining = briefs[len(sample):]
        if matched:
            matched.extend(remaining)
        else:
            mismatched.extend(remaining)

        mismatch_rate = len(mismatched) / len(briefs) if briefs else 0.0
        return mismatch_rate, matched, mismatched

    @staticmethod
    def _parse_relevance_response(raw: str, count: int) -> dict[int, bool]:
        """Parse LLM relevance judgement into {id: bool} mapping."""
        result: dict[int, bool] = {}

        text = raw.strip()
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        md = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if md:
            text = md.group(1).strip()

        start = text.find("{")
        if start == -1:
            return result
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[start : i + 1]
                    break

        text = text.replace("\u201c", '"').replace("\u201d", '"')

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            for m in re.finditer(
                r'"id"\s*:\s*(\d+)\s*,\s*"relevant"\s*:\s*(true|false)', text, re.I
            ):
                result[int(m.group(1))] = m.group(2).lower() == "true"
            return result

        for item in data.get("results", []):
            item_id = item.get("id")
            relevant = item.get("relevant")
            if isinstance(item_id, int) and isinstance(relevant, bool):
                result[item_id] = relevant

        return result

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
