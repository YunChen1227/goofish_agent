from __future__ import annotations

import asyncio
import json
import random
import re
from typing import TYPE_CHECKING

from goofish_agent.hooks.base import BaseHook, HookContext, HookResult
from goofish_agent.skills.keyword_retry_skill import (
    KeywordRetryInput,
    KeywordRetryResult,
    KeywordRetrySkill,
)

if TYPE_CHECKING:
    from goofish_agent.ai.llm_client import LLMClient
    from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief
    from goofish_agent.models.task import Task
    from goofish_agent.platform.base import PlatformClient


class ProductSearchHook(BaseHook):
    """Hook: 商品搜索切面。

    行为（对应需求 2 / 2.1 / 2.2 / 2.3）：
      1. 调用 ``client.search`` 拉取一批商品简介，必须等到"所有商品的标题 /
         简介被解析完"才进入下一步（``client.search`` 内部已由 Parser 保证）。
      2. 用 LLM 对已解析的前若干条样本做相关性批量判定：
         ``match_ratio = matched / total``。
      3. 若 ``match_ratio >= MATCH_THRESHOLD``（默认 0.8），取前 ``TOP_N``（默认 10）
         作为候选写入 ``ctx.data['top_candidates']``，进入下一步。
      4. 否则调用 :class:`KeywordRetrySkill` 改写关键词，最多重试 ``MAX_RETRIES``
         （默认 3）次；每次用新关键词重新搜索并重复第 2 步。
      5. 达到重试上限仍未达标 → ``HookResult.abort`` 通知上层退出整个流程，
         告知用户"无法检测到足够商品样本"。

    该 Hook 只使用 :class:`KeywordRetrySkill` 一个 Skill；相关性判定因为是
    "批量 bool 标注"、与单品精细相似度语义不同，直接调用 ``ctx.llm`` 完成，
    后续若出现专门的 ``RelevanceCheckSkill`` 可原位替换。
    """

    name = "product_search"
    description = "等待搜索结果解析完成，命中率未达阈值则触发关键词重写，达标后取 Top-N 候选"

    DEFAULT_MATCH_THRESHOLD = 0.8
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_TOP_N = 10
    DEFAULT_SAMPLE_SIZE = 20
    DEFAULT_ANTI_BOT_DELAY_RANGE: tuple[float, float] = (5.0, 10.0)

    def __init__(
        self,
        keyword_retry_skill: KeywordRetrySkill | None = None,
        *,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        max_retries: int = DEFAULT_MAX_RETRIES,
        top_n: int = DEFAULT_TOP_N,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        anti_bot_delay_range: tuple[float, float] = DEFAULT_ANTI_BOT_DELAY_RANGE,
    ) -> None:
        self._skill = keyword_retry_skill
        self._match_threshold = match_threshold
        self._max_retries = max_retries
        self._top_n = top_n
        self._sample_size = sample_size
        self._anti_bot_delay_range = anti_bot_delay_range

    async def run(self, ctx: HookContext) -> HookResult:
        task = ctx.require_task()
        if ctx.client is None:
            return HookResult.abort(error="missing_dependency:client")
        if ctx.llm is None:
            return HookResult.abort(error="missing_dependency:llm")

        skill = self._resolve_skill(ctx)

        current_keywords: str = task.keywords
        optimization_history: list[KeywordRetryResult] = []
        briefs: list["ProductBrief"] = []
        final_match_ratio = 0.0

        for attempt in range(1 + self._max_retries):
            self._log_info(
                f"搜索轮次 {attempt + 1}/{1 + self._max_retries} | 关键词='{current_keywords}'"
            )
            briefs = await ctx.client.search(current_keywords, self._build_filters(task))
            self._log_info(f"搜索到 {len(briefs)} 个结果（已完成标题/简介解析）")

            if briefs:
                match_ratio, matched = await self._classify_relevance(
                    ctx.llm, briefs, task.keywords
                )
                final_match_ratio = match_ratio
                self._log_info(
                    f"[命中率] {match_ratio:.0%} | "
                    f"匹配 {len(matched)} / 采样 {min(len(briefs), self._sample_size)} | "
                    f"阈值 {self._match_threshold:.0%}"
                )
                if match_ratio >= self._match_threshold:
                    top = matched[: self._top_n]
                    ctx.data["top_candidates"] = top
                    ctx.data["final_keywords"] = current_keywords
                    ctx.data["match_ratio"] = match_ratio
                    self._log_info(
                        f"命中率达标，选出 Top-{len(top)} 候选进入下一步分析"
                    )
                    return HookResult.ok(
                        top_candidates=top,
                        final_keywords=current_keywords,
                        match_ratio=match_ratio,
                        attempts=attempt + 1,
                    )
            else:
                self._log_warning(f"关键词 '{current_keywords}' 无搜索结果")

            if attempt >= self._max_retries:
                break
            if skill is None:
                self._log_warning(
                    "缺少 KeywordRetrySkill，无法进行关键词重写，停止重试"
                )
                break

            next_keywords = await self._optimize_keywords(
                skill,
                original=task.keywords,
                current=current_keywords,
                briefs=briefs,
                history=optimization_history,
            )
            if not next_keywords or next_keywords == current_keywords:
                self._log_warning(
                    "KeywordRetrySkill 未给出新的关键词，停止重试"
                )
                break

            self._log_info(
                f"关键词重写（第 {attempt + 1}/{self._max_retries} 次）: "
                f"'{current_keywords}' → '{next_keywords}'"
            )
            current_keywords = next_keywords
            await self._anti_bot_sleep()

        error = (
            f"insufficient_matches: 经过 {1 + self._max_retries} 轮搜索与关键词重写，"
            f"命中率仍为 {final_match_ratio:.0%} < {self._match_threshold:.0%}，"
            f"无法检测到足够商品样本"
        )
        self._log_error(error)
        return HookResult.abort(
            error=error,
            match_ratio=final_match_ratio,
            last_keywords=current_keywords,
        )

    def _resolve_skill(self, ctx: HookContext) -> KeywordRetrySkill | None:
        if self._skill is not None:
            return self._skill
        skill = ctx.skills.get(KeywordRetrySkill.name)
        return skill if isinstance(skill, KeywordRetrySkill) else None

    @staticmethod
    def _build_filters(task: "Task") -> dict:
        return {
            "min_price": getattr(task, "min_price", None),
            "max_price": getattr(task, "max_price", None),
            "location": getattr(task, "location", None),
        }

    async def _optimize_keywords(
        self,
        skill: KeywordRetrySkill,
        *,
        original: str,
        current: str,
        briefs: list["ProductBrief"],
        history: list[KeywordRetryResult],
    ) -> str:
        sample_titles = [b.title for b in briefs[:15] if getattr(b, "title", None)]
        params = KeywordRetryInput(
            original_keywords=original,
            current_keywords=current,
            sample_titles=sample_titles,
            previous_attempts=[
                h.optimized_keywords for h in history if h.optimized_keywords
            ],
        )
        try:
            result = await skill.execute(params)
        except NotImplementedError:
            self._log_warning("KeywordRetrySkill.execute 尚未实现，跳过重写")
            return ""
        except Exception as e:
            self._log_warning(f"KeywordRetrySkill 执行异常: {e}")
            return ""
        history.append(result)
        return result.optimized_keywords or ""

    async def _classify_relevance(
        self,
        llm: "LLMClient",
        briefs: list["ProductBrief"],
        user_keywords: str,
    ) -> tuple[float, list["ProductBrief"]]:
        """用 LLM 批量判定每个标题是否与用户搜索意图相关。

        返回 (match_ratio, matched_briefs)。``match_ratio`` 在整个采样窗口上计算，
        ``matched_briefs`` 保留原始顺序，且把采样之外的 brief 追加在已命中列表尾部，
        以便 Top-N 切片能继续流向下游。
        """
        sample = briefs[: self._sample_size]
        titles_block = "\n".join(
            f"  {i}. {getattr(b, 'title', '') or ''}" for i, b in enumerate(sample, 1)
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
            raw = await llm.generate(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
            )
        except Exception as e:
            self._log_warning(f"相关性判定 LLM 调用失败 ({e})，回退到全部保留")
            return 1.0, list(briefs)

        relevant_map = self._parse_relevance_response(raw, len(sample))

        matched: list["ProductBrief"] = []
        mismatch_count = 0
        for i, brief in enumerate(sample, 1):
            if relevant_map.get(i, True):
                matched.append(brief)
            else:
                mismatch_count += 1

        remaining = briefs[len(sample):]
        matched.extend(remaining)

        denom = len(sample) if sample else 1
        match_ratio = (len(sample) - mismatch_count) / denom
        return match_ratio, matched

    @staticmethod
    def _parse_relevance_response(raw: str, count: int) -> dict[int, bool]:
        text = raw.strip()
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        md = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if md:
            text = md.group(1).strip()

        start = text.find("{")
        if start == -1:
            return {}
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
            result: dict[int, bool] = {}
            for m in re.finditer(
                r'"id"\s*:\s*(\d+)\s*,\s*"relevant"\s*:\s*(true|false)', text, re.I
            ):
                result[int(m.group(1))] = m.group(2).lower() == "true"
            return result

        result = {}
        for item in data.get("results", []):
            item_id = item.get("id")
            relevant = item.get("relevant")
            if isinstance(item_id, int) and isinstance(relevant, bool):
                result[item_id] = relevant
        return result

    async def _anti_bot_sleep(self) -> None:
        lo, hi = self._anti_bot_delay_range
        delay = random.uniform(lo, hi)
        self._log_info(f"反爬等待 {delay:.1f}s 后重试搜索……")
        await asyncio.sleep(delay)
