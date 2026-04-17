"""Agent Skills: 面向不同执行阶段的可插拔技能集合。

每个 Skill 封装一段独立的 Agent 推理能力（prompt + 模型调用 + 结构化解析），
由上层（hooks / searcher / assessor / negotiator 等）按阶段按需调用。

约定：
  * 所有 Skill 继承 :class:`BaseSkill`，通过 ``execute(params)`` 调用。
  * 所有 Skill 的返回值继承 :class:`SkillResult`。
"""

from goofish_agent.skills.base import BaseSkill, SkillResult
from goofish_agent.skills.keyword_retry_skill import (
    KeywordRetryInput,
    KeywordRetryResult,
    KeywordRetrySkill,
)
from goofish_agent.skills.login_detection_skill import (
    LoginDetectionInput,
    LoginDetectionResult,
    LoginDetectionSkill,
)
from goofish_agent.skills.similarity_match_skill import (
    SimilarityMatchInput,
    SimilarityMatchResult,
    SimilarityMatchSkill,
)

__all__ = [
    "BaseSkill",
    "SkillResult",
    "KeywordRetrySkill",
    "KeywordRetryInput",
    "KeywordRetryResult",
    "LoginDetectionSkill",
    "LoginDetectionInput",
    "LoginDetectionResult",
    "SimilarityMatchSkill",
    "SimilarityMatchInput",
    "SimilarityMatchResult",
]
