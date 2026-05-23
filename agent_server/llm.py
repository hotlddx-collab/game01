"""DeepSeek LLM 封装（用 openai SDK 兼容模式）。"""
from __future__ import annotations

import os
from typing import List, Dict, Optional

from openai import AsyncOpenAI


class LLMClient:
    """异步 LLM 客户端。线程/事件循环安全。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        if not api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，请配置 .env")

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 200,
        temperature: float = 0.9,
    ) -> str:
        """messages 形如 [{role, content}, ...]，返回 assistant 文本。"""
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        return (choice.message.content or "").strip()
