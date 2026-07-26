"""code agent —— 把 naive_agent 手写的 while loop 改成 langgraph 的 StateGraph。

对比 naive_agent/xagent.py 看：核心逻辑一模一样（调模型 → 有没有 tool_calls → 执行
工具 → 塞回 messages → 循环），只是显式画成了图：
    agent 节点 --(有 tool_calls)--> tools 节点 --> agent 节点
    agent 节点 --(没有 tool_calls 或到达 max_turns)--> END
换来的东西：graph.stream() 原生支持流式查看每一步状态、checkpointer 原生支持"跑到一半
断了、换个 thread_id 还能接着问"。换不来的东西：loop 本身还是那个 loop——框架是包装，
不是本质（对照 mini-swe-agent：100 行、bash-only，SWE-bench verified >74%）。

运行前：
    cp .env.example .env   # 填好 key；程序会自动加载

用法（--prompt 既可以是一句话，也可以是写着任务描述的文件路径）：
    python -m code_agent.graph --prompt "用 CSV 数据做一个经营数据看板"
    python -m code_agent.graph --prompt code_agent/prompts/dashboard.txt --model deepseek/deepseek-v4-pro
    python -m code_agent.graph --prompt code_agent/prompts/dashboard.txt --skill   # 挂载 list_skills/load_skill
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from code_agent.tools import DISPATCH, DISPATCH_NO_SKILLS, TOOLS, TOOLS_NO_SKILLS
from lib.llm import chat

MAX_TURNS = 30  # 默认轮数上限；CLI / run() 的 --max-turns 可覆盖
# code_agent/prompts/dashboard.txt 里写死了 "output/code/dashboard.html"——多次运行同一个
# 任务会互相覆盖，调参数、对比效果时看不到历史版本。时间戳目录由框架在这里生成、替换进
# task 文本，模型看到的已经是一个具体路径（例如 output/code/20260726_164500/），不需要
# 自己算时间戳、也不知道有这道替换存在。没有这个前缀的自由任务不受影响。
OUTPUT_PREFIX = "output/code/"


def _stamp_output_path(task: str) -> str:
    run_id = time.strftime("%Y%m%d_%H%M%S")
    return task.replace(OUTPUT_PREFIX, f"{OUTPUT_PREFIX}{run_id}/")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "RULES.md"), encoding="utf-8") as _f:
    RULES = _f.read()

SYSTEM_PROMPT = "你是 code agent，一个 coding agent。以下是你必须遵守的规则：\n\n" + RULES

# 运行期可切换：--skill 挂 list_skills/load_skill；--model 覆盖默认模型；--max-turns 覆盖轮数。
# 用模块级变量而不是把参数一路传进每个图节点，是因为 langgraph 节点函数签名只认 state——
# 这跟 naive_agent.xagent.run() 直接传参数的写法不同，是 StateGraph 节点模型带来的限制。
_active_tools = TOOLS_NO_SKILLS
_active_dispatch = DISPATCH_NO_SKILLS
_model_override: str | None = None
_max_turns = MAX_TURNS


def _append(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """messages 字段的 reducer：每个节点返回的新消息追加到历史，而不是覆盖。
    不用 langgraph 自带的 add_messages，是因为那个 reducer 要求 LangChain 的消息对象；
    我们直接复用 lib.llm/litellm 返回的原生 OpenAI 格式 dict，没必要多一层转换。
    """
    return left + right


class AgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _append]
    turn: int


def agent_node(state: AgentState) -> dict[str, Any]:
    response = chat(state["messages"], tools=_active_tools, model=_model_override, max_tokens=8192)
    msg = response.choices[0].message
    return {"messages": [msg.model_dump()], "turn": state["turn"] + 1}


def tools_node(state: AgentState) -> dict[str, Any]:
    last = state["messages"][-1]
    results = []
    for call in last["tool_calls"]:
        name = call["function"]["name"]
        print(f"[调用] {name}({call['function']['arguments'][:200]})")
        try:
            # 参数解析失败（常见于模型生成超长内联脚本时输出被截断）也是一种
            # observation，交回给模型自己决定，不能让它直接崩掉整个 loop。
            args = json.loads(call["function"]["arguments"])
            result = _active_dispatch[name](**args)
        except Exception as e:  # 停止条件二：工具报错（含参数解析失败）交回给模型自己决定
            result = f"工具执行出错：{type(e).__name__}: {e}"
        print(f"[结果] {str(result)[:300]}")
        results.append({
            "role": "tool", "tool_call_id": call["id"],
            "content": str(result)[:8000],  # 粗暴截断，防 context 爆炸
        })
    return {"messages": results}


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    tool_calls = last.get("tool_calls") if isinstance(last, dict) else getattr(last, "tool_calls", None)
    if state["turn"] >= _max_turns:
        return END
    if tool_calls:
        return "tools"
    return END


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=MemorySaver())


def run(
    task: str,
    thread_id: str = "default",
    *,
    model: str | None = None,
    use_skills: bool = False,
    max_turns: int = MAX_TURNS,
) -> str:
    global _active_tools, _active_dispatch, _model_override, _max_turns
    _active_tools = TOOLS if use_skills else TOOLS_NO_SKILLS
    _active_dispatch = DISPATCH if use_skills else DISPATCH_NO_SKILLS
    _model_override = model
    _max_turns = max_turns
    task = _stamp_output_path(task)

    app = build_graph()
    state: AgentState = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ],
        "turn": 0,
    }
    config = {"configurable": {"thread_id": thread_id}}
    final_content = ""
    last_turn = 0
    # stream_mode="updates"：每个节点跑完就吐一次增量，这是 langgraph 原生的 streaming——
    # 生产系统里前端可以边算边显示，不用等整个 graph 跑完才有响应。
    for update in app.stream(state, config=config, stream_mode="updates"):
        for node, delta in update.items():
            if node == "agent":
                last_turn = delta.get("turn", last_turn)
                msg = delta["messages"][-1]
                content = msg.get("content") if isinstance(msg, dict) else msg.content
                if content:
                    final_content = content
    # 停止条件三：轮数用尽——route_after_agent 里 turn>=_max_turns 强制走 END，哪怕最后一条
    # 消息还带着 tool_calls（意味着模型本来还打算再做一步，比如"再跑一遍 verify.py"）。
    # 跟 naive_agent 的 while loop 打印一样的提示，方便看出是不是卡在这个边界条件上。
    if last_turn >= _max_turns:
        print(f"\n[达到最大轮数 {_max_turns}，强制停止——最后一条回复可能还没做完]")
    print(f"\n[code agent 最终回复]\n{final_content}")
    return final_content


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="code agent（LangGraph StateGraph 版）")
    parser.add_argument("--prompt", help="任务描述，或写着任务描述的文件路径")
    parser.add_argument("--model", help="覆盖 .env 里的 MODEL，例如 deepseek/deepseek-v4-pro")
    parser.add_argument("--skill", action="store_true",
                        help="挂载 list_skills/load_skill；默认不挂，对比技能发现机制时加上")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS,
                        help=f"工具循环轮数上限（默认 {MAX_TURNS}）")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    task = args.prompt or "列出 workspace 里有哪些文件，并说明每个文件是干什么的"
    if os.path.isfile(task):
        task = open(task, encoding="utf-8").read()
    run(task, model=args.model, use_skills=args.skill, max_turns=args.max_turns)
