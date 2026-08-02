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
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

        if not api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，请配置 .env")

        # 原来没设超时，SDK 默认约 600s，一旦请求卡住（网络抖动/API 侧挂起）
        # 前端会一直显示"正在思考..."长达数分钟。收紧到 20s + 少重试，
        # 让调用方（agent.py）的 except 分支能及时兜底出文案。
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=20.0,
            max_retries=1,
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 200,
        temperature: float = 0.9,
    ) -> str:
        """messages 形如 [{role, content}, ...]，返回 assistant 文本。"""
        # deepseek-v4 系列默认开启推理（reasoning_content 会吃掉 max_tokens，
        # 导致小额度下正文为空）。游戏对话不需要链式推理 → 关闭 thinking。
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = resp.choices[0]
        return (choice.message.content or "").strip()
