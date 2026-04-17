from __future__ import annotations

from dataclasses import dataclass, field

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.skills.base import BaseSkill, SkillResult


@dataclass
class KeywordRetryInput:
    """关键词拆解重试 Skill 的输入。

    Attributes:
        original_keywords: 用户最初输入的搜索关键词（不会被覆盖，用于回溯意图）。
        current_keywords: 本轮实际用于搜索的关键词（可能是上一轮优化后的结果）。
        sample_titles: 当前搜索返回的前 N 条商品标题样本，用于让模型判断命中情况。
        previous_attempts: 历史重试记录，避免模型重复给出相同的失败关键词。
        extra_context: 供上层塞入的附加上下文（如类目、价格区间等），可选。
    """

    original_keywords: str
    current_keywords: str = ""
    sample_titles: list[str] = field(default_factory=list)
    previous_attempts: list[str] = field(default_factory=list)
    extra_context: dict = field(default_factory=dict)


@dataclass
class KeywordRetryResult(SkillResult):
    """关键词拆解重试 Skill 的输出。

    Attributes:
        optimized_keywords: 推荐用于下一次搜索的关键词；失败时保持为空或回退到原关键词。
        intent_analysis: 对用户真实购买意图的拆解（品类 / 具体商品 / 意图概述）。
        keyword_decomposition: 原关键词的有效词 / 噪音词 / 纠错说明等。
        search_strategies: 候选搜索策略列表，每项包含 keywords + rationale。
        coverage_check: 对优化结果是否覆盖原始意图的自检。
        should_retry: 上层是否应当使用新关键词继续重试（若模型认为无需再试可置 False）。
    """

    optimized_keywords: str = ""
    intent_analysis: dict = field(default_factory=dict)
    keyword_decomposition: dict = field(default_factory=dict)
    search_strategies: list[dict] = field(default_factory=list)
    coverage_check: dict = field(default_factory=dict)
    should_retry: bool = True


class KeywordRetrySkill(BaseSkill):
    """Skill: 搜索命中率低时，拆解用户提示词并生成重试关键词。

    使用场景：
      * 搜索返回 0 条结果。
      * 搜索有结果但与用户真实意图的相关度过低（mismatch_rate 超过阈值）。

    该 Skill 的职责：
      1. 理解用户原始关键词背后的真实购买意图（可能含缩写、错别字、行话）。
      2. 拆解关键词为“有效词 / 噪音词”，必要时纠错。
      3. 结合当前搜索样本与历史失败尝试，生成下一次检索建议。
      4. 对优化结果做一次“覆盖度自检”，确保不丢失核心意图。
    """

    name = "keyword_retry"
    description = "搜索未命中时，拆解用户意图并生成下一轮优化后的重试关键词"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def execute(self, params: KeywordRetryInput) -> KeywordRetryResult:  # type: ignore[override]
        # TODO: 在此实现关键词拆解与重试生成逻辑
        #   1. 根据 params 构造 system / user prompt
        #   2. 调用 self._llm.generate(...)
        #   3. 使用 self._safe_json_loads(...) 解析响应
        #   4. 填充 KeywordRetryResult 并返回
        raise NotImplementedError(
            "KeywordRetrySkill.execute 尚未实现，请在此编写关键词拆解与重试逻辑。"
        )
