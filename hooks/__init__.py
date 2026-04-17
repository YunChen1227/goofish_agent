"""Agent Hooks: 面向 Agent 运行流程不同阶段的可插拔切面。

一个 Hook 表示 Agent 在流程里"需要等待/校验/分叉"的一个点位：
  * 登录验证  -> :class:`LoginVerificationHook`
  * 商品搜索  -> :class:`ProductSearchHook`
  * 商品详情  -> :class:`ProductDetailHook`
  * …… 未来可继续扩展（收藏 / 议价 / 通知等）

所有 Hook 都继承 :class:`BaseHook`，输入统一为 :class:`HookContext`，
输出统一为 :class:`HookResult`，便于在 :class:`HookPipeline` 里串联调度。
新增 Hook 时，只需：
  1. 新建 ``xxx_hook.py`` 文件，定义一个 :class:`BaseHook` 子类；
  2. 在此 ``__init__`` 中导出；
  3. 在上层装配处 ``pipeline.register(XxxHook(...))`` 即可。
"""

from goofish_agent.hooks.base import (
    BaseHook,
    HookContext,
    HookPipeline,
    HookResult,
)
from goofish_agent.hooks.detail_hook import ProductDetailHook
from goofish_agent.hooks.login_hook import LoginVerificationHook
from goofish_agent.hooks.search_hook import ProductSearchHook

__all__ = [
    "BaseHook",
    "HookContext",
    "HookResult",
    "HookPipeline",
    "LoginVerificationHook",
    "ProductSearchHook",
    "ProductDetailHook",
]
