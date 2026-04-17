from __future__ import annotations

from dataclasses import dataclass, field

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.skills.base import BaseSkill, SkillResult


@dataclass
class SimilarityMatchInput:
    """相似度 / 匹配度 Skill 的输入。

    商品侧（来自平台抓取）：
        product_title:        商品标题。
        product_description:  商品详情描述（卖家发布文本）。
        product_images:       商品实物图 URL 或 base64。

    参考侧（来自用户任务配置，任一维度均可为空）：
        reference_keywords:    用户最初的搜索关键词。
        reference_description: 用户对想要商品的自然语言描述。
        reference_images:      用户提供的参考图片。

    其它：
        weights: 文本 / 图片维度的权重，默认 {"text": 0.5, "image": 0.5}，
                 上层可按场景（如仅有参考图、仅有关键词）动态调整。
    """

    product_title: str
    product_description: str = ""
    product_images: list[str] = field(default_factory=list)

    reference_keywords: str | None = None
    reference_description: str | None = None
    reference_images: list[str] = field(default_factory=list)

    weights: dict = field(default_factory=lambda: {"text": 0.5, "image": 0.5})


@dataclass
class SimilarityMatchResult(SkillResult):
    """相似度 / 匹配度 Skill 的输出。

    Attributes:
        overall_score:    综合匹配度（0~1），按 weights 聚合文本与图像分数。
        text_match_score: 文本维度（标题 + 描述 vs 关键词 / 用户描述）的匹配度（0~1）。
        image_match_score: 图片维度（商品图 vs 参考图）的匹配度（0~1）；无参考图时为 None。
        matched_aspects:  一致的关键特征（型号 / 颜色 / 配件 / 外观等）。
        differences:      显著差异项。
        reasoning:        人类可读的判定理由。
        passed:           是否通过匹配（由上层阈值决定，可由 Skill 预填默认建议）。
    """

    overall_score: float = 0.0
    text_match_score: float = 0.0
    image_match_score: float | None = None
    matched_aspects: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    reasoning: str = ""
    passed: bool = False


class SimilarityMatchSkill(BaseSkill):
    """Skill: 评估商品简介与图像，与用户提供的关键词 / 描述 / 参考图的匹配度。

    使用场景：
      * 搜索结果预筛选：判断平台返回的商品标题是否符合用户意图。
      * 候选复筛：综合商品详情文本 + 实拍图 与 用户参考信息做相似度评估。
      * 图片对比：对比商品实拍图与买家参考图，输出 0~1 的匹配分。

    该 Skill 的职责：
      1. 分别计算文本维度与图片维度的匹配度（缺失的维度可跳过）。
      2. 按 weights 聚合为综合 overall_score。
      3. 给出一致项 / 差异项与人类可读理由，便于日志追踪。
    """

    name = "similarity_match"
    description = "综合商品简介与图像和用户关键词/描述/参考图，输出多维度相似度与匹配度"

    def __init__(self, llm: LLMClient, vlm: VLMClient) -> None:
        self._llm = llm
        self._vlm = vlm

    async def execute(self, params: SimilarityMatchInput) -> SimilarityMatchResult:  # type: ignore[override]
        # TODO: 在此实现相似度 / 匹配度评估逻辑
        #   1. 文本相似度: 用 self._llm 判定 title+description 与
        #      reference_keywords/reference_description 的匹配度
        #   2. 图片相似度: 若 reference_images 非空，调用 self._vlm
        #      评估 product_images vs reference_images
        #   3. 按 params.weights 聚合 overall_score
        #   4. 填充 matched_aspects / differences / reasoning
        #   5. 返回 SimilarityMatchResult
        raise NotImplementedError(
            "SimilarityMatchSkill.execute 尚未实现，请在此编写相似度 / 匹配度评估逻辑。"
        )
