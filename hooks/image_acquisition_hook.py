"""图片获取 Hook.

对接 :class:`ImageAcquisitionSkill`，在品相鉴定阶段前批量把候选商品的
图片落到本地（HTTP 下载失败时自动降级到 Playwright 元素截图 / OS 截屏）。

使用方式有两种：
  1. 作为 ``HookPipeline`` 中的一环，从 ``ctx.data['candidates']`` 读取候选，
     把每个候选的 base64 图列表写回 ``ctx.data['local_images_per_candidate']``；
  2. 作为 Assessor 的合作者（通过 ``acquire_for_candidate`` 被直接调用），
     这样可以复用现有 Assessor 的逐个评估循环，避免破坏其已有实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from goofish_agent.hooks.base import BaseHook, HookContext, HookResult
from goofish_agent.skills.image_acquisition_skill import (
    ImageAcquisitionInput,
    ImageAcquisitionResult,
    ImageAcquisitionSkill,
)

if TYPE_CHECKING:
    from goofish_agent.models.candidate import ProductCandidate


class ImageAcquisitionHook(BaseHook):
    """Hook: 多策略把商品图片拉到本地，供 VLM 品相鉴定使用."""

    name = "image_acquisition"
    description = "调用 ImageAcquisitionSkill 批量获取候选商品图片 (下载 / 截图降级)"

    SKILL_NAME = "image_acquisition"

    def __init__(self, skill: ImageAcquisitionSkill | None = None) -> None:
        self._skill = skill

    async def run(self, ctx: HookContext) -> HookResult:
        candidates: list["ProductCandidate"] = ctx.data.get("candidates", [])
        if not candidates:
            return HookResult.ok(acquired=0)

        skill = self._resolve_skill(ctx)
        page = ctx.page
        bucket: dict[str, ImageAcquisitionResult] = {}
        total_ok = 0
        for cand in candidates:
            result = await self.acquire_for_candidate(
                candidate=cand, skill=skill, page=page
            )
            bucket[str(cand.id)] = result
            total_ok += len(result.local_paths)

        ctx.data["local_images_per_candidate"] = bucket
        self._log_info(
            f"图片获取汇总: {len(bucket)} 个候选 / 共成功 {total_ok} 张"
        )
        return HookResult.ok(acquired=total_ok, candidates=len(bucket))

    async def acquire_for_candidate(
        self,
        *,
        candidate: "ProductCandidate",
        skill: ImageAcquisitionSkill | None = None,
        page: Any | None = None,
    ) -> ImageAcquisitionResult:
        skill = skill or self._skill
        if skill is None:
            raise RuntimeError(
                "ImageAcquisitionHook 缺少 ImageAcquisitionSkill，"
                "请在构造或 ctx.skills 中提供"
            )

        product_url = getattr(candidate, "url", "") or ""
        params = ImageAcquisitionInput(
            image_urls=list(candidate.images or []),
            product_url=product_url,
            page=page,
        )
        return await skill.execute(params)

    def _resolve_skill(self, ctx: HookContext) -> ImageAcquisitionSkill:
        if self._skill is not None:
            return self._skill
        return ctx.require_skill(self.SKILL_NAME)  # type: ignore[return-value]
