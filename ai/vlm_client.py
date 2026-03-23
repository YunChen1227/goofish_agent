from __future__ import annotations

import json

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
            api_key=api_key or s.openai_api_key,
            base_url=base_url or s.openai_base_url,
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
        return json.loads(resp.choices[0].message.content or "{}")

    @retry(max_retries=2, exceptions=(Exception,))
    async def match_images(
        self, product_images: list[str], reference_images: list[str]
    ) -> float:
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
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            return 0.0

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
