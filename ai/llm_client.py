from __future__ import annotations

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
            api_key=api_key or s.openai_api_key,
            base_url=base_url or s.openai_base_url,
        )

    @retry(max_retries=3, exceptions=(Exception,))
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> str:
        msgs = [{"role": "system", "content": system_prompt}, *messages]
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=msgs,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    @retry(max_retries=3, exceptions=(Exception,))
    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
