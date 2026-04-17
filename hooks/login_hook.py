from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from goofish_agent.hooks.base import BaseHook, HookContext, HookResult
from goofish_agent.skills.login_detection_skill import (
    LoginDetectionInput,
    LoginDetectionResult,
    LoginDetectionSkill,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


DEFAULT_LOGIN_BUTTON_TEXTS: tuple[str, ...] = ("登录", "Log in", "Sign in")
DEFAULT_MODAL_SELECTORS: tuple[str, ...] = (
    '[class*="login"][class*="modal"]',
    '[class*="login"][class*="dialog"]',
    '[class*="login"][class*="popup"]',
    '[class*="modal-mask"]',
    '[class*="overlay"][class*="login"]',
    '[class*="baxia"]',
    'iframe[src*="login"]',
)


class LoginVerificationHook(BaseHook):
    """Hook: 登录验证切面。

    行为（对应需求 1 / 1.1）：
      1. 打开平台主页 ``base_url``；
      2. 在 ``max_wait_seconds`` 时限内循环轮询，每次：
         a) 采集 URL、标题、正文片段、DOM 线索；
         b) 调用 :class:`LoginDetectionSkill`（大模型参与判定）；
         c) 若模型判定 ``is_home_ready=True`` 且 ``is_login_required=False`` 则通过；
      3. 超时则整体失败并请求中止后续 Hook（``should_continue=False``）。

    这个 Hook 取代了原先散在 ``AuthManager._wait_until_logged_in`` 里的规则式
    判断，将"弹窗嗅探 + 文案匹配 + 人类可读理由"的工作全部交给 Skill 完成，
    便于统一维护提示词与容错策略。
    """

    name = "login_verification"
    description = "打开网页后持续轮询页面，使用 LLM 判定是否已回到购物主页首页"

    DEFAULT_MAX_WAIT_SECONDS = 300
    DEFAULT_POLL_INTERVAL = 3.0
    DEFAULT_BODY_SNIPPET_CHARS = 2000

    def __init__(
        self,
        skill: LoginDetectionSkill | None = None,
        *,
        base_url: str | None = None,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        login_button_texts: tuple[str, ...] = DEFAULT_LOGIN_BUTTON_TEXTS,
        modal_selectors: tuple[str, ...] = DEFAULT_MODAL_SELECTORS,
        body_snippet_chars: int = DEFAULT_BODY_SNIPPET_CHARS,
    ) -> None:
        self._skill = skill
        self._base_url = base_url
        self._max_wait_seconds = max_wait_seconds
        self._poll_interval = poll_interval
        self._login_button_texts = login_button_texts
        self._modal_selectors = modal_selectors
        self._body_snippet_chars = body_snippet_chars

    async def run(self, ctx: HookContext) -> HookResult:
        page = ctx.require_page()
        skill = self._resolve_skill(ctx)
        if skill is None:
            return HookResult.abort(
                error="missing_skill:login_detection（需在 ctx.skills 或构造参数中提供）"
            )

        base_url = self._resolve_base_url(ctx)
        if base_url:
            self._log_info(f"打开平台首页: {base_url}")
            try:
                await page.goto(base_url, wait_until="load")
            except Exception as e:
                self._log_warning(f"导航到主页失败: {e}；将继续基于当前页面检测")
            await asyncio.sleep(3)

        self._log_info(
            f"开始轮询登录状态 | 超时 {self._max_wait_seconds:.0f}s | "
            f"间隔 {self._poll_interval:.1f}s"
        )

        deadline = time.monotonic() + self._max_wait_seconds
        last_result: LoginDetectionResult | None = None
        poll_count = 0

        while True:
            poll_count += 1
            params = await self._collect_page_features(page, ctx)
            result = await skill.execute(params)
            last_result = result

            self._log_info(
                f"[poll #{poll_count}] login_required={result.is_login_required} | "
                f"home_ready={result.is_home_ready} | conf={result.confidence:.2f}"
                + (f" | reason={result.reasoning}" if result.reasoning else "")
            )

            if result.is_home_ready and not result.is_login_required:
                self._log_info("登录通过，主界面就绪，进入下一步")
                return HookResult.ok(
                    polls=poll_count,
                    detected_prompts=result.detected_prompts,
                    reasoning=result.reasoning,
                )

            if time.monotonic() >= deadline:
                break

            await asyncio.sleep(self._poll_interval)

        error = (
            f"login_timeout: 超过 {self._max_wait_seconds:.0f}s 仍未回到主页；"
            f"最近一次模型理由: {last_result.reasoning if last_result else '(无)'}"
        )
        self._log_error(error)
        return HookResult.abort(
            error=error,
            polls=poll_count,
            last_prompts=last_result.detected_prompts if last_result else [],
        )

    def _resolve_skill(self, ctx: HookContext) -> LoginDetectionSkill | None:
        if self._skill is not None:
            return self._skill
        skill = ctx.skills.get(LoginDetectionSkill.name)
        return skill if isinstance(skill, LoginDetectionSkill) else None

    def _resolve_base_url(self, ctx: HookContext) -> str | None:
        if self._base_url:
            return self._base_url
        if ctx.client is not None:
            return getattr(ctx.client.config, "base_url", None)
        return None

    async def _collect_page_features(
        self, page: "Page", ctx: HookContext
    ) -> LoginDetectionInput:
        url = ""
        title = ""
        body = ""
        has_button = False
        has_modal = False

        try:
            url = page.url or ""
        except Exception:
            pass
        try:
            title = await page.title()
        except Exception:
            pass
        try:
            body = await page.inner_text("body")
        except Exception:
            body = ""
        if body and len(body) > self._body_snippet_chars:
            body = body[: self._body_snippet_chars]

        for text in self._login_button_texts:
            try:
                loc = page.get_by_text(text, exact=True).first
                if await loc.is_visible(timeout=500):
                    has_button = True
                    break
            except Exception:
                continue

        for sel in self._modal_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    has_modal = True
                    break
            except Exception:
                continue

        extra: dict = {}
        if ctx.client is not None:
            extra["platform"] = getattr(ctx.client.config, "name", "")
            extra["platform_display"] = getattr(ctx.client.config, "display_name", "")

        return LoginDetectionInput(
            url=url,
            page_title=title,
            body_text_snippet=body,
            has_login_button=has_button,
            has_modal=has_modal,
            extra_context=extra,
        )
