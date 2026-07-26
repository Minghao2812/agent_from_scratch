"""MCP client：给 xagent 风格的同步 tool dispatch 包一层，屏蔽掉 MCP 协议本身是异步的。

每次调用都新起一个 stdio 子进程连 mcp_server.py、握手、调用、退出——最简单、最不容易
出隐藏状态的写法，代价是每次搜索多付出约 1 秒的子进程启动开销。生产系统会保持一个
长连接 session 复用，但那要求跨多次同步函数调用维持一个后台事件循环，其生命周期管理
的复杂度与"演示 MCP 协议最小闭环"这个目标不匹配，此处从简。
"""

from __future__ import annotations

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_SERVER_PARAMS = StdioServerParameters(
    command="python",
    args=["-m", "deep_research_agent.mcp_server"],
    env=dict(os.environ),  # 子进程要看得到 BOCHA_API_KEY
)


async def _call_web_search(query: str, count: int) -> str:
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("web_search", {"query": query, "count": count})
            return "".join(getattr(part, "text", "") for part in result.content)


def web_search_via_mcp(query: str, count: int = 5) -> str:
    """同步入口：xagent 风格的 dispatch 函数只认同步调用。"""
    return asyncio.run(_call_web_search(query, count))
