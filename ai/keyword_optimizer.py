from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loguru import logger

from goofish_agent.ai.llm_client import LLMClient


KEYWORD_AGENT_SYSTEM_PROMPT = """\
你是一个二手商品搜索关键词优化 Agent。用户在二手平台搜索商品，搜索结果不理想。
你的任务是分析用户的原始关键词，理解其真实购买意图，并生成更优的搜索关键词。

请严格按以下 JSON 格式输出，不要输出任何其他内容:
{
  "intent_analysis": {
    "product_category": "商品大类（如: 游戏王卡牌、手机、电脑配件）",
    "specific_item": "具体商品名称或系列",
    "user_intent": "一句话概括用户想买什么"
  },
  "keyword_decomposition": {
    "meaningful_tokens": ["从原始关键词中识别出的有意义的词/短语"],
    "noise_tokens": ["识别出的噪音词、错别字、无意义字符"],
    "correction_notes": "对错别字或拼写问题的说明（如有）"
  },
  "search_strategies": [
    {
      "keywords": "建议的搜索关键词",
      "rationale": "为什么推荐这个关键词组合",
      "expected_coverage": "预期能覆盖用户意图中的哪些方面"
    }
  ],
  "coverage_check": {
    "original_intent_covered": true,
    "missing_aspects": ["未覆盖的用户意图（如有）"],
    "recommendation": "最终推荐使用的关键词"
  }
}"""


@dataclass
class OptimizationResult:
    """Structured output from one round of keyword optimization."""
    original_keywords: str
    optimized_keywords: str
    intent_analysis: dict = field(default_factory=dict)
    keyword_decomposition: dict = field(default_factory=dict)
    search_strategies: list[dict] = field(default_factory=list)
    coverage_check: dict = field(default_factory=dict)
    raw_response: str = ""


class KeywordOptimizer:
    """Agent-style keyword optimizer that decomposes user intent, generates
    candidate keywords, and verifies coverage before recommending a search
    query.  Each call produces structured reasoning visible in the logs."""

    MAX_RETRIES = 3

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def optimize(
        self,
        original_keywords: str,
        sample_titles: list[str] | None = None,
        search_history: list[OptimizationResult] | None = None,
    ) -> OptimizationResult:
        user_message = self._build_user_message(
            original_keywords, sample_titles, search_history
        )

        logger.info(
            f"[关键词Agent] 开始推理 | 原始关键词: '{original_keywords}' | "
            f"搜索结果样本: {len(sample_titles or [])} 条 | "
            f"历史优化轮次: {len(search_history or [])} 次"
        )

        raw = await self._call_llm(user_message)
        result = self._parse_response(raw, original_keywords)

        self._log_reasoning(result)
        return result

    def _build_user_message(
        self,
        original_keywords: str,
        sample_titles: list[str] | None,
        search_history: list[OptimizationResult] | None,
    ) -> str:
        parts: list[str] = [f"原始搜索关键词: {original_keywords}"]

        if search_history:
            history_lines: list[str] = []
            for i, h in enumerate(search_history, 1):
                history_lines.append(
                    f"  第{i}次: '{h.optimized_keywords}' → "
                    f"{'有结果但不匹配' if sample_titles else '无结果'}"
                )
            parts.append("已尝试过的关键词（均未成功）:\n" + "\n".join(history_lines))

        if sample_titles:
            titles_text = "\n".join(f"  - {t}" for t in sample_titles[:15])
            parts.append(f"当前搜索结果中的商品标题（样本）:\n{titles_text}")
        else:
            parts.append("当前搜索结果: 无（平台返回 0 条结果）")

        parts.append(
            "请分析上述信息，给出优化后的搜索关键词。"
            "注意：必须保留用户的核心购买意图，不能遗漏关键商品特征。"
        )
        return "\n\n".join(parts)

    async def _call_llm(self, user_message: str) -> str:
        try:
            return await self._llm.generate(
                system_prompt=KEYWORD_AGENT_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.3,
            )
        except Exception as e:
            logger.error(f"[关键词Agent] 调用大模型失败: {e}")
            return ""

    @staticmethod
    def _extract_json(raw: str) -> str | None:
        """Best-effort extraction of JSON object from LLM output that may
        contain thinking tags, markdown fences, or preamble text."""
        text = raw
        # Strip <think>...</think> blocks (qwen reasoning mode)
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        # Strip markdown ```json ... ``` fences
        md_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if md_match:
            text = md_match.group(1).strip()
        # Find the first top-level { ... } block
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    @staticmethod
    def _parse_response(raw: str, original_keywords: str) -> OptimizationResult:
        result = OptimizationResult(
            original_keywords=original_keywords,
            optimized_keywords=original_keywords,
            raw_response=raw,
        )

        if not raw.strip():
            return result

        json_str = KeywordOptimizer._extract_json(raw)
        if not json_str:
            logger.warning(
                f"[关键词Agent] 无法从大模型响应中提取 JSON: {raw[:200]}"
            )
            return result

        # Normalize smart quotes that some models produce
        json_str = json_str.replace("\u201c", '"').replace("\u201d", '"')
        json_str = json_str.replace("\u2018", "'").replace("\u2019", "'")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[关键词Agent] JSON 解析失败 ({e}), 尝试正则提取 recommendation"
            )
            m = re.search(r'"recommendation"\s*:\s*"([^"]+)"', json_str)
            if m:
                result.optimized_keywords = m.group(1).strip()
                logger.info(
                    f"[关键词Agent] 正则提取到推荐关键词: '{result.optimized_keywords}'"
                )
            return result

        result.intent_analysis = data.get("intent_analysis", {})
        result.keyword_decomposition = data.get("keyword_decomposition", {})
        result.search_strategies = data.get("search_strategies", [])
        result.coverage_check = data.get("coverage_check", {})

        recommendation = (
            result.coverage_check.get("recommendation", "")
            or (
                result.search_strategies[0].get("keywords", "")
                if result.search_strategies
                else ""
            )
        )
        if recommendation:
            result.optimized_keywords = recommendation.strip().strip("\"'")

        return result

    @staticmethod
    def _log_reasoning(result: OptimizationResult) -> None:
        indent = "    "
        lines = [
            f"[关键词Agent] ===== 推理过程 =====",
            f"[关键词Agent] 原始关键词: '{result.original_keywords}'",
        ]

        ia = result.intent_analysis
        if ia:
            lines.append(f"[关键词Agent] 📌 意图分析:")
            lines.append(f"{indent}商品大类: {ia.get('product_category', '-')}")
            lines.append(f"{indent}具体商品: {ia.get('specific_item', '-')}")
            lines.append(f"{indent}用户意图: {ia.get('user_intent', '-')}")

        kd = result.keyword_decomposition
        if kd:
            lines.append(f"[关键词Agent] 🔍 关键词拆解:")
            lines.append(
                f"{indent}有效词: {kd.get('meaningful_tokens', [])}"
            )
            lines.append(f"{indent}噪音词: {kd.get('noise_tokens', [])}")
            if kd.get("correction_notes"):
                lines.append(
                    f"{indent}纠错说明: {kd['correction_notes']}"
                )

        if result.search_strategies:
            lines.append(f"[关键词Agent] 💡 搜索策略候选:")
            for i, s in enumerate(result.search_strategies, 1):
                lines.append(
                    f"{indent}策略{i}: '{s.get('keywords', '')}'"
                )
                lines.append(
                    f"{indent}  理由: {s.get('rationale', '-')}"
                )
                lines.append(
                    f"{indent}  覆盖: {s.get('expected_coverage', '-')}"
                )

        cc = result.coverage_check
        if cc:
            lines.append(f"[关键词Agent] ✅ 覆盖度验证:")
            lines.append(
                f"{indent}原始意图已覆盖: "
                f"{'是' if cc.get('original_intent_covered') else '否'}"
            )
            if cc.get("missing_aspects"):
                lines.append(
                    f"{indent}未覆盖方面: {cc['missing_aspects']}"
                )
            lines.append(
                f"{indent}最终推荐: '{cc.get('recommendation', '-')}'"
            )

        lines.append(
            f"[关键词Agent] ✨ 优化结果: '{result.original_keywords}' → "
            f"'{result.optimized_keywords}'"
        )
        lines.append(f"[关键词Agent] ===== 推理结束 =====")

        logger.info("\n".join(lines))
