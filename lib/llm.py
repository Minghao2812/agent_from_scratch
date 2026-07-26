"""统一 LLM 调用

用 litellm 而不直接用 openai SDK，是为了让换模型只是改一个字符串：
    MODEL=deepseek/deepseek-v4-flash   # 默认，个人账号
    MODEL=deepseek/deepseek-v4-pro     # 需要更强推理时手动切换
换成 Qwen/GLM 等其它 OpenAI 兼容平台，把 MODEL 换成 "openai/<model>" 并设置
OPENAI_API_BASE / OPENAI_API_KEY 即可，代码不用改。

用法：
    from lib.llm import chat
    resp = chat(messages, tools=TOOLS)
    msg = resp.choices[0].message   # .content / .tool_calls，和 openai SDK 一致
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import litellm
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

litellm.drop_params = True  # 网关/模型未必支持全部 OpenAI 参数，多传的直接丢弃而不是报错
litellm.suppress_debug_info = True

DEFAULT_MODEL = os.environ.get("MODEL", "deepseek/deepseek-v4-flash")
DEFAULT_MAX_TOKENS = 4096
_RETRYABLE = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.Timeout,
)


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = 3,
    timeout: float = 60.0,
    **kwargs: Any,
):
    """调用 LLM，返回 litellm 的 ModelResponse（.choices[0].message 里有 content/tool_calls）。"""
    model = model or DEFAULT_MODEL
    last: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return litellm.completion(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs,
            )
        except _RETRYABLE as e:
            last = e
            if attempt >= max_retries:
                raise
            time.sleep(min(4 * (2**attempt), 30))
    raise last  # pragma: no cover
