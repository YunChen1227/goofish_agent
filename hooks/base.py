from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import Page

    from goofish_agent.ai.llm_client import LLMClient
    from goofish_agent.ai.vlm_client import VLMClient
    from goofish_agent.models.task import Task
    from goofish_agent.platform.base import PlatformClient
    from goofish_agent.skills.base import BaseSkill


@dataclass
class HookContext:
    """Agent 流程里所有 Hook 之间共享的上下文。

    设计原则：
      * 所有 Hook 只读/追加 ``data``，尽量不修改平台资源本身，便于拼装。
      * 可选依赖一律允许为 ``None``，让 Hook 自己兜底或在缺依赖时跳过执行，
        以支持"抽出来单独跑单元测试"的场景。

    Attributes:
        page:    Playwright 当前主 Page；登录/搜索/详情 Hook 通常都会用到。
        task:    本次 Agent 执行对应的业务 Task 对象（含关键词、参考图等）。
        client:  平台客户端，Hook 可通过它调用 ``search`` / ``get_product_detail`` 等。
        llm:     文本大模型客户端，供需要 LLM 参与的 Hook 使用。
        vlm:     视觉大模型客户端，供需要图像判断的 Hook 使用。
        skills:  按 ``skill.name`` 索引的 Skill 表，Hook 通过名字向上层要技能。
        data:    跨 Hook 的共享状态容器（例如搜索 Hook 会往里写 ``top_candidates``）。
    """

    page: "Page | None" = None
    task: "Task | None" = None
    client: "PlatformClient | None" = None
    llm: "LLMClient | None" = None
    vlm: "VLMClient | None" = None
    skills: dict[str, "BaseSkill"] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def require_page(self) -> "Page":
        if self.page is None:
            raise RuntimeError("HookContext.page is required but not provided")
        return self.page

    def require_task(self) -> "Task":
        if self.task is None:
            raise RuntimeError("HookContext.task is required but not provided")
        return self.task

    def require_skill(self, name: str) -> "BaseSkill":
        skill = self.skills.get(name)
        if skill is None:
            raise RuntimeError(f"HookContext missing required skill: '{name}'")
        return skill


@dataclass
class HookResult:
    """Hook 执行结束后的结构化结果。

    Attributes:
        success:          Hook 本身是否成功完成（仅代表"执行没异常"）。
        should_continue:  上层 Pipeline 是否应继续执行后续 Hook；
                          对应 2.3 "80%不符合则退出整个流程" 的语义。
        error:            失败原因（人类可读），用于日志/通知。
        data:             Hook 输出（例如搜索 Hook 会塞 ``top_candidates``）。
    """

    success: bool = True
    should_continue: bool = True
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, **data: Any) -> "HookResult":
        return cls(success=True, should_continue=True, data=dict(data))

    @classmethod
    def abort(cls, error: str, **data: Any) -> "HookResult":
        """Hook 主动中止整个 Agent 流程（例如样本不足 / 登录超时）。"""
        return cls(success=False, should_continue=False, error=error, data=dict(data))

    @classmethod
    def fail(cls, error: str, **data: Any) -> "HookResult":
        """Hook 自身失败但允许上层决定是否继续。"""
        return cls(success=False, should_continue=True, error=error, data=dict(data))


class BaseHook(ABC):
    """所有 Agent Hook 的抽象基类。

    一个 Hook 表示 Agent 运行流程里的"一个阶段切面"：
      * 登录切面：打开站点后等待用户登录完成再进入首页；
      * 搜索切面：等待搜索页解析完毕、命中率达标后再进入下一步；
      * 详情切面：点入商品后等待品相分析完成；
      * …… 未来可继续扩展（收藏 / 沟通 / 议价 / 通知 等）。

    子类约定：
      1. 覆盖 :pyattr:`name` 与 :pyattr:`description`，用于 Pipeline 调度与日志。
      2. 实现 :pymeth:`run`，只接收 :class:`HookContext`，返回 :class:`HookResult`。
      3. 不直接 ``raise``；把失败/中止语义放入 ``HookResult``，
         方便上层按 ``should_continue`` 做统一流程控制。
    """

    name: str = "base_hook"
    description: str = ""

    @abstractmethod
    async def run(self, ctx: HookContext) -> HookResult:
        raise NotImplementedError

    def _log_info(self, message: str) -> None:
        logger.info(f"[Hook:{self.name}] {message}")

    def _log_warning(self, message: str) -> None:
        logger.warning(f"[Hook:{self.name}] {message}")

    def _log_error(self, message: str) -> None:
        logger.error(f"[Hook:{self.name}] {message}")


class HookPipeline:
    """按注册顺序调度 Hook 的流水线。

    提供两种执行方式：
      * :pymeth:`run_one` —— 按名字单独触发某个 Hook（例如只跑登录切面）；
      * :pymeth:`run_all` —— 顺序执行所有已注册 Hook，遇到 ``should_continue=False``
        时中止并返回已累计的结果。

    这个轻量级调度器足以覆盖"登录 → 搜索 → 详情 → …"线性流程；
    若未来出现条件分支，可在此类上扩展 ``run_if`` / ``run_until`` 等策略，
    而无需改动 Hook 本身的实现。
    """

    def __init__(self) -> None:
        self._hooks: dict[str, BaseHook] = {}
        self._order: list[str] = []

    def register(self, hook: BaseHook) -> "HookPipeline":
        if hook.name in self._hooks:
            raise ValueError(f"Hook '{hook.name}' already registered")
        self._hooks[hook.name] = hook
        self._order.append(hook.name)
        return self

    def get(self, name: str) -> BaseHook:
        if name not in self._hooks:
            raise KeyError(f"Hook '{name}' not registered")
        return self._hooks[name]

    def names(self) -> list[str]:
        return list(self._order)

    async def run_one(self, name: str, ctx: HookContext) -> HookResult:
        hook = self.get(name)
        logger.info(f"[HookPipeline] ▶ 执行 Hook: {hook.name}")
        try:
            result = await hook.run(ctx)
        except Exception as e:
            logger.exception(f"[HookPipeline] ✗ Hook '{hook.name}' 执行异常: {e}")
            return HookResult.fail(error=f"{type(e).__name__}: {e}")
        status = "✓" if result.success else "✗"
        logger.info(
            f"[HookPipeline] {status} Hook '{hook.name}' 完成 | "
            f"continue={result.should_continue}"
            + (f" | error={result.error}" if result.error else "")
        )
        return result

    async def run_all(self, ctx: HookContext) -> list[HookResult]:
        results: list[HookResult] = []
        for name in self._order:
            result = await self.run_one(name, ctx)
            results.append(result)
            if not result.should_continue:
                logger.warning(
                    f"[HookPipeline] Hook '{name}' 请求中止流程，"
                    f"剩余 {len(self._order) - len(results)} 个 Hook 不再执行"
                )
                break
        return results
