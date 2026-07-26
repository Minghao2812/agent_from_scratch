"""一个真实的本地 MCP server：把 Bocha 联网检索包成一个 MCP tool。

与 function calling 的区别：Function Calling / Tool Use 是「进程内直接调用一个 Python
函数」，MCP 是「通过标准协议（这里用 stdio transport）跨进程调用一个工具」。同样是给
模型一个检索能力，前者零协议开销；后者的价值是工具可以跑在别的进程/别的机器/别的语言
里，换任何支持 MCP 的 agent 框架都能直接复用同一个 server。

单独启动可以验证协议本身是否正常（不经过 agent）：
    python -m deep_research_agent.mcp_server
（进程会阻塞等待 stdio 输入，Ctrl+C 退出；正常应该由 MCP client 拉起，不是人直接跑）
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

mcp = FastMCP("bocha-search")


@mcp.tool()
def web_search(query: str, count: int = 5) -> str:
    """联网检索（Bocha），返回 JSON 字符串：[{title, url, snippet}, ...]。

    这是这个 MCP server 目前唯一暴露的工具；deep_research_agent 通过
    mcp_client.py 里的 MCP client 调用它，而不是直接 import requests。
    """
    import json

    key = os.environ.get("BOCHA_API_KEY")
    if not key:
        return json.dumps({"error": "未配置 BOCHA_API_KEY，请填写项目根目录的 .env"})
    base = os.environ.get("BOCHA_BASE_URL", "https://api.bochaai.com").rstrip("/")
    resp = requests.post(
        f"{base}/v1/web-search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"query": query, "summary": True, "count": count},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    values = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    results = [
        {
            "title": v.get("name", ""),
            "url": v.get("url", ""),
            "snippet": (v.get("summary") or v.get("snippet") or "")[:500],
        }
        for v in values
    ]
    return json.dumps(results, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
