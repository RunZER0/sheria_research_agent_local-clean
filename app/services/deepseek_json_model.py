from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI


class DeepSeekJSONModel:
    """
    Async JSON-only DeepSeek model wrapper.

    Used by DocumentRevisionAgent to return valid DocumentSpec JSON.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.15,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeekJSONModel.")

        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("DEEPSEEK_DOCUMENT_MODEL", "deepseek-chat")
        self.reasoning_effort = reasoning_effort or os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
        self.max_tokens = max_tokens or int(os.getenv("DEEPSEEK_DOCUMENT_MAX_TOKENS", "24000"))
        self.temperature = temperature

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            stream=False,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
            extra_body={
                "thinking": {
                    "type": "enabled",
                }
            },
        )

        content = response.choices[0].message.content or ""

        if not content.strip():
            raise RuntimeError("DeepSeek returned empty JSON content.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DeepSeek returned invalid JSON: {exc}\n\n{content[:1000]}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("DeepSeek JSON response must be an object.")

        return parsed
