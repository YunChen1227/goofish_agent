from __future__ import annotations

import json

from loguru import logger
from openai import AsyncOpenAI

from goofish_agent.config.settings import get_settings
from goofish_agent.utils.retry import retry

from .prompts.assessment import (
    IMAGE_MATCH_PROMPT,
    build_assessment_prompt,
    build_assessment_user_message,
)


class VLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        self._model = model or s.vlm_model
        self._client = AsyncOpenAI(
            api_key=api_key or s.vlm_api_key,
            base_url=base_url or s.vlm_base_url,
        )

    @retry(max_retries=3, exceptions=(Exception,))
    async def assess_product(
        self,
        images: list[str],
        description: str,
        reference_images: list[str] | None = None,
    ) -> dict:
        has_ref = bool(reference_images)
        system_prompt = build_assessment_prompt(description, has_reference=has_ref)
        user_text = build_assessment_user_message(description)

        logger.info(
            f"[VLM 品相鉴定] 发送请求 | 模型: {self._model} | "
            f"商品图片: {len(images)}张 | "
            f"参考图片: {len(reference_images) if reference_images else 0}张"
        )
        logger.debug(f"[VLM 品相鉴定] 商品描述: {description[:200]}")
        logger.debug(f"[VLM 品相鉴定] System Prompt:\n{system_prompt[:500]}")

        content: list[dict] = [{"type": "text", "text": user_text}]
        content.extend(self._build_image_content(images, label="商品图片"))
        if reference_images:
            content.append({"type": "text", "text": "以下是买家提供的参考图片:"})
            content.extend(self._build_image_content(reference_images, label="参考图片"))

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=2048,
        )
        raw_content = resp.choices[0].message.content or "{}"
        result = json.loads(raw_content)

        logger.info(
            f"[VLM 品相鉴定] 模型返回结果 | "
            f"品相等级: {result.get('condition_grade', 'N/A')} | "
            f"品相评分: {result.get('condition_score', 'N/A')}/10 | "
            f"描述一致性: {result.get('description_match', 'N/A')}/10"
        )
        if result.get("defects"):
            for i, defect in enumerate(result["defects"], 1):
                logger.info(
                    f"[VLM 品相鉴定]   瑕疵{i}: "
                    f"位置={defect.get('location', '未知')} | "
                    f"严重度={defect.get('severity', '未知')} | "
                    f"{defect.get('description', '')}"
                )
        else:
            logger.info("[VLM 品相鉴定]   瑕疵: 无")
        if result.get("risk_flags"):
            logger.warning(f"[VLM 品相鉴定]   风险标记: {result['risk_flags']}")
        if result.get("accessories_missing"):
            logger.info(f"[VLM 品相鉴定]   缺失配件: {result['accessories_missing']}")
        if result.get("accessories_confirmed"):
            logger.info(f"[VLM 品相鉴定]   已确认配件: {result['accessories_confirmed']}")
        if result.get("reference_match"):
            ref = result["reference_match"]
            logger.info(
                f"[VLM 品相鉴定]   参考图对比: 匹配度={ref.get('score', 'N/A')} | "
                f"一致项={ref.get('matched_aspects', [])} | "
                f"差异项={ref.get('differences', [])}"
            )
        logger.info(f"[VLM 品相鉴定]   总结: {result.get('summary', '无')}")

        return result

    @retry(max_retries=2, exceptions=(Exception,))
    async def match_images(
        self, product_images: list[str], reference_images: list[str]
    ) -> float:
        logger.debug(
            f"[VLM 图片匹配] 发送请求 | "
            f"商品图片: {len(product_images)}张 | 参考图片: {len(reference_images)}张"
        )

        content: list[dict] = [{"type": "text", "text": IMAGE_MATCH_PROMPT}]
        content.append({"type": "text", "text": "商品实物图片:"})
        content.extend(self._build_image_content(product_images))
        content.append({"type": "text", "text": "买家参考图片:"})
        content.extend(self._build_image_content(reference_images))

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            max_tokens=16,
        )
        raw = (resp.choices[0].message.content or "0").strip()
        try:
            score = max(0.0, min(1.0, float(raw)))
        except ValueError:
            logger.warning(f"[VLM 图片匹配] 无法解析模型返回值: '{raw}'，默认为0")
            score = 0.0

        logger.info(f"[VLM 图片匹配] 匹配度: {score:.2f}")
        return score

    @staticmethod
    def _build_image_content(
        images: list[str], label: str | None = None
    ) -> list[dict]:
        blocks: list[dict] = []
        for img in images:
            if img.startswith(("http://", "https://")):
                blocks.append(
                    {"type": "image_url", "image_url": {"url": img, "detail": "high"}}
                )
            else:
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img}",
                            "detail": "high",
                        },
                    }
                )
        return blocks
