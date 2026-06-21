import json
import re
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from .config import Settings


class DeepSeekClient:
    def __init__(self, settings: Settings):
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is missing. Add it to .env.")
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

    def _base_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "temperature": 0.15,
        }

        if self.settings.deepseek_thinking.lower() == "enabled":
            kwargs["reasoning_effort"] = self.settings.deepseek_reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        return kwargs

    async def complete(self, messages: list[dict[str, str]], *, response_format: str = "text", **kwargs) -> str:
        kwargs_local = self._base_kwargs()
        if response_format == "json":
            kwargs_local["response_format"] = {"type": "json_object"}

        # Forward any extra kwargs (max_tokens, etc.) to the API call
        kwargs_local.update(kwargs)

        response = await self.client.chat.completions.create(
            messages=messages,
            stream=False,
            **kwargs_local,
        )
        return response.choices[0].message.content or ""

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        kwargs = self._base_kwargs()
        stream = await self.client.chat.completions.create(
            messages=messages,
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response.")

    return json.loads(match.group(0))
