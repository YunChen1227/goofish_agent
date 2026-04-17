from __future__ import annotations

from dataclasses import dataclass, field

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.skills.base import BaseSkill, SkillResult


@dataclass
class LoginDetectionInput:
    """登录检测 Skill 的输入。

    这些字段由 :class:`LoginVerificationHook` 从浏览器 Page 采集后传入，
    Skill 本身不触碰 Playwright，便于离线回放与测试。

    Attributes:
        url:              当前 Page 的完整 URL（带 query），用于识别 ``/login``、
                          ``/auth``、``captcha`` 等跳转。
        page_title:       ``<title>`` 文本，辅助判别"登录 - XXX"这类页面。
        body_text_snippet: 页面可见文案摘要（建议截取前 1500~2000 字符），
                          用于让模型读中/英文登录提示（如"请登录"/"Sign in"）。
        has_login_button: 由 DOM 嗅探得到的"明显登录按钮"存在与否；作为轻量先验。
        has_modal:        由 DOM 嗅探得到的"登录弹窗/遮罩层"存在与否。
        extra_context:    可选附加上下文（平台名、期望首页 URL 等）。
    """

    url: str
    page_title: str = ""
    body_text_snippet: str = ""
    has_login_button: bool = False
    has_modal: bool = False
    extra_context: dict = field(default_factory=dict)


@dataclass
class LoginDetectionResult(SkillResult):
    """登录检测 Skill 的输出。

    Attributes:
        is_login_required: 是否仍处于"需要登录"的状态（True 表示未登录/被弹窗拦住）。
        is_home_ready:     是否已处于可正常购物的主页/首页状态。
        detected_prompts:  模型识别到的登录相关提示文案（中英文均可）。
        reasoning:         判定理由，便于日志追踪。
        confidence:        0~1 的主观置信度，低置信度时上层可延长轮询间隔。
    """

    is_login_required: bool = False
    is_home_ready: bool = False
    detected_prompts: list[str] = field(default_factory=list)
    reasoning: str = ""
    confidence: float = 0.0


class LoginDetectionSkill(BaseSkill):
    """Skill: 通过大模型判定当前页面是否处于"需要登录"的状态。

    使用场景：
      * 打开网页后出现登录弹窗 / 登录页跳转 / 扫码登录页；
      * 页面文案中出现"请登录 / 短信登录 / 扫码登录 / Sign in / Log in"等提示；
      * 登录成功后页面回到真正的购物主页首页，Skill 需给出 ``is_home_ready=True``。

    该 Skill 的职责：
      1. 接收已由 Hook 采集好的"页面特征"（URL + 标题 + 正文摘要 + DOM 标志位）；
      2. 用 LLM 做一次结构化判断，输出 ``is_login_required`` / ``is_home_ready``；
      3. 同时返回识别到的具体登录提示文案与自然语言理由，方便排障。

    仅做"判定"，不触碰浏览器；真正的"等待 / 轮询 / 跳转"行为放在 Hook 里。
    """

    name = "login_detection"
    description = "基于页面 URL、标题、正文与 DOM 线索，用大模型判定是否仍在登录拦截态"

    SYSTEM_PROMPT = (
        "你是一个电商网站登录状态判定助手。输入是用户打开一个网站后的页面观测信息，\n"
        "包括当前 URL、页面标题、正文片段，以及两个 DOM 嗅探得到的布尔线索：\n"
        "  - has_login_button: 页面是否存在明显的登录按钮；\n"
        "  - has_modal: 页面是否出现登录弹窗/遮罩层。\n\n"
        "你要综合所有信息判断：\n"
        "  1. 当前是否仍然处于「需要用户登录」的状态（弹窗拦截 / 跳转到登录页 /\n"
        "     出现'请登录'、'扫码登录'、'Sign in'、'Log in'、'verify'、'captcha' 等中英文提示）。\n"
        "  2. 页面是否已经回到可正常浏览商品的主页/首页状态。\n\n"
        "请严格按以下 JSON 输出，不要输出任何其他内容：\n"
        "{\n"
        '  "is_login_required": true/false,\n'
        '  "is_home_ready": true/false,\n'
        '  "detected_prompts": ["识别到的原文提示1", "..."],\n'
        '  "reasoning": "一两句话说明理由",\n'
        '  "confidence": 0.0~1.0\n'
        "}"
    )

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def execute(self, params: LoginDetectionInput) -> LoginDetectionResult:  # type: ignore[override]
        user_message = self._build_user_message(params)

        try:
            raw = await self._llm.generate(
                system_prompt=self.SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.1,
            )
        except Exception as e:
            self._log_error(f"LLM 调用失败: {e}")
            return LoginDetectionResult(
                success=False,
                error=f"llm_error: {e}",
                is_login_required=params.has_login_button or params.has_modal,
                is_home_ready=False,
                reasoning="LLM 调用失败，回退到 DOM 线索",
                confidence=0.2,
            )

        data = self._safe_json_loads(raw) or {}
        if not data:
            self._log_warning("无法解析模型输出，回退到 DOM 线索")
            return LoginDetectionResult(
                success=False,
                error="parse_error",
                raw_response=raw,
                is_login_required=params.has_login_button or params.has_modal,
                is_home_ready=not (params.has_login_button or params.has_modal),
                reasoning="模型输出无法解析，使用 DOM 嗅探结果兜底",
                confidence=0.2,
            )

        prompts = data.get("detected_prompts") or []
        if not isinstance(prompts, list):
            prompts = [str(prompts)]

        result = LoginDetectionResult(
            success=True,
            raw_response=raw,
            is_login_required=bool(data.get("is_login_required", False)),
            is_home_ready=bool(data.get("is_home_ready", False)),
            detected_prompts=[str(p) for p in prompts],
            reasoning=str(data.get("reasoning", "")),
            confidence=float(data.get("confidence", 0.5) or 0.0),
        )
        self._log_info(
            f"判定: login_required={result.is_login_required} | "
            f"home_ready={result.is_home_ready} | conf={result.confidence:.2f} | "
            f"prompts={result.detected_prompts}"
        )
        return result

    @staticmethod
    def _build_user_message(params: LoginDetectionInput) -> str:
        body_snippet = (params.body_text_snippet or "").strip()
        if len(body_snippet) > 2000:
            body_snippet = body_snippet[:2000] + "……(截断)"

        lines = [
            f"URL: {params.url}",
            f"页面标题: {params.page_title or '(空)'}",
            f"DOM 嗅探 - has_login_button: {params.has_login_button}",
            f"DOM 嗅探 - has_modal: {params.has_modal}",
        ]
        if params.extra_context:
            lines.append(f"附加上下文: {params.extra_context}")
        lines.append("")
        lines.append("页面正文片段：")
        lines.append(body_snippet or "(空)")
        return "\n".join(lines)
