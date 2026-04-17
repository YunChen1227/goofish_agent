from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class SkillResult:
    """所有 Skill 输出的基类。

    每个 Skill 可继承此类扩展自己的结构化字段。保留 ``raw_response`` 与
    ``success`` 方便统一打日志与失败回退。
    """

    success: bool = True
    error: str = ""
    raw_response: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class BaseSkill(ABC):
    """Agent 技能抽象基类。

    一个 Skill 表示 Agent 在某个执行阶段可以独立调用的一段推理能力：
    拥有自己的 prompt、模型调用方式、响应解析与结构化日志输出。

    子类约定：
      * 覆盖 :pyattr:`name` 与 :pyattr:`description`，用于日志与调度识别。
      * 实现 :pymeth:`execute`，接收强类型入参、返回继承自 :class:`SkillResult`
        的结构化对象。
      * 如果需要多轮调用或重试，请在 :pymeth:`execute` 内部自行管理，
        Skill 对外暴露的仍是“一次语义完整的能力调用”。
    """

    name: str = "base_skill"
    description: str = ""

    @abstractmethod
    async def execute(self, *args: Any, **kwargs: Any) -> SkillResult:
        """执行 Skill 并返回结构化结果。"""
        raise NotImplementedError

    def _log_info(self, message: str) -> None:
        logger.info(f"[Skill:{self.name}] {message}")

    def _log_warning(self, message: str) -> None:
        logger.warning(f"[Skill:{self.name}] {message}")

    def _log_error(self, message: str) -> None:
        logger.error(f"[Skill:{self.name}] {message}")

    @staticmethod
    def _extract_json(raw: str) -> str | None:
        """从可能包含思考标签 / markdown 围栏 / 前置文本的模型输出中提取首个 JSON 对象。"""
        if not raw:
            return None

        text = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()

        md_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if md_match:
            text = md_match.group(1).strip()

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

    @classmethod
    def _safe_json_loads(cls, raw: str) -> dict | None:
        """宽松解析：处理智能引号，抽取 JSON 片段，失败返回 None。"""
        json_str = cls._extract_json(raw)
        if not json_str:
            return None

        json_str = (
            json_str.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None
