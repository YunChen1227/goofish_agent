from __future__ import annotations

from loguru import logger
from openai import AsyncOpenAI

from goofish_agent.config.settings import get_settings
from goofish_agent.utils.retry import retry


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        self._model = model or s.llm_model
        self._client = AsyncOpenAI(
            api_key=api_key or s.llm_api_key,
            base_url=base_url or s.llm_base_url,
        )

    @retry(max_retries=3, exceptions=(Exception,))
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> str:
        logger.debug(
            f"[LLM chat] 发送请求 | 模型: {self._model} | "
            f"消息数: {len(messages)} | temperature: {temperature}"
        )
        logger.debug(f"[LLM chat] System: {system_prompt[:200]}")
        msgs = [{"role": "system", "content": system_prompt}, *messages]
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=msgs,
            temperature=temperature,
        )
        result = resp.choices[0].message.content or ""
        logger.debug(f"[LLM chat] 模型回复: {result[:300]}")
        return result

    @retry(max_retries=3, exceptions=(Exception,))
    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
    ) -> str:
        logger.debug(
            f"[LLM generate] 发送请求 | 模型: {self._model} | temperature: {temperature}"
        )
        logger.debug(f"[LLM generate] System: {system_prompt[:200]}")
        logger.debug(f"[LLM generate] User: {user_message[:300]}")
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )
        result = resp.choices[0].message.content or ""
        logger.debug(f"[LLM generate] 模型回复: {result[:300]}")
        return result
