from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from goofish_agent.hooks.base import BaseHook, HookContext, HookResult

if TYPE_CHECKING:
    from goofish_agent.goofish_platform.parsers.detail_parser import ProductDetail
    from goofish_agent.goofish_platform.parsers.search_parser import ProductBrief


class ProductDetailHook(BaseHook):
    """Hook: 点击商品详情页后的切面（需求 3 的占位实现）。

    目标流程：
      1. 从上游 Hook（通常是 :class:`ProductSearchHook`）取出 Top-N 候选
         （``ctx.data['top_candidates']``）。
      2. 逐个 ``client.get_product_detail(product_id)``，必须等到页面
         标题 / 描述 / 图片 / 卖家信用等详情信息全部解析完毕。
      3. 调用品相鉴定 Skill（待接入；例如 VLM + LLM 的组合）对商品做品相评估，
         并把结果写入 ``ctx.data['detail_reports']``。
      4. 若无任何商品通过品相鉴定，``abort`` 退出流程。

    当前版本只做"等待详情解析 + 结构化收集"；品相鉴定 Skill 接入后再在
    :pymeth:`_assess_detail` 里调用即可，不需要改动 Hook 的外形。
    """

    name = "product_detail"
    description = "逐个打开 Top-N 商品详情，等待详情解析完毕后进行品相鉴定"

    DEFAULT_BETWEEN_DELAY_RANGE: tuple[float, float] = (1.5, 3.5)

    def __init__(
        self,
        *,
        assess_skill: Any | None = None,
        between_delay_range: tuple[float, float] = DEFAULT_BETWEEN_DELAY_RANGE,
    ) -> None:
        self._assess_skill = assess_skill
        self._between_delay_range = between_delay_range

    async def run(self, ctx: HookContext) -> HookResult:
        if ctx.client is None:
            return HookResult.abort(error="missing_dependency:client")

        top_candidates: list["ProductBrief"] = ctx.data.get("top_candidates", [])
        if not top_candidates:
            return HookResult.abort(
                error="no_top_candidates: ProductDetailHook 需要上游 Hook 先写入 top_candidates"
            )

        details: list["ProductDetail"] = []
        reports: list[dict] = []
        lo, hi = self._between_delay_range

        for idx, brief in enumerate(top_candidates, 1):
            product_id = getattr(brief, "product_id", "") or ""
            if not product_id:
                self._log_warning(f"[{idx}/{len(top_candidates)}] 缺少 product_id，跳过")
                continue

            self._log_info(
                f"[{idx}/{len(top_candidates)}] 打开详情: {getattr(brief, 'title', '')}"
            )
            try:
                detail = await ctx.client.get_product_detail(product_id)
            except Exception as e:
                self._log_warning(
                    f"[{idx}/{len(top_candidates)}] 详情获取异常 {product_id}: {e}"
                )
                continue
            if detail is None:
                self._log_warning(
                    f"[{idx}/{len(top_candidates)}] 详情解析失败: {product_id}"
                )
                continue

            details.append(detail)

            report = await self._assess_detail(ctx, detail)
            if report is not None:
                reports.append(report)

            if idx < len(top_candidates):
                await asyncio.sleep((lo + hi) / 2)

        ctx.data["details"] = details
        ctx.data["detail_reports"] = reports

        if not details:
            return HookResult.abort(
                error="no_detail_resolved: 所有候选的详情页均解析失败"
            )

        self._log_info(
            f"详情阶段完成 | 解析成功 {len(details)}/{len(top_candidates)} | "
            f"品相报告 {len(reports)} 份"
        )
        return HookResult.ok(
            details=details,
            detail_reports=reports,
            resolved=len(details),
            requested=len(top_candidates),
        )

    async def _assess_detail(
        self, ctx: HookContext, detail: "ProductDetail"
    ) -> dict | None:
        """品相鉴定占位实现。

        后续可在此处调用品相鉴定 Skill（输入：``detail.title`` / ``detail.description``
        / ``detail.images`` + ``task.reference_images``；输出：品相等级 / 评分 /
        瑕疵清单 / 风险标记）。当前仅做结构化占位，保证 Hook 本体的接口稳定。
        """
        if self._assess_skill is None:
            return None
        try:
            return await self._assess_skill.execute(  # type: ignore[no-any-return]
                detail=detail,
                task=ctx.task,
            )
        except NotImplementedError:
            self._log_warning("品相鉴定 Skill 尚未实现，跳过品相评估")
            return None
        except Exception as e:
            self._log_warning(f"品相鉴定失败 {getattr(detail, 'product_id', '')}: {e}")
            return None
