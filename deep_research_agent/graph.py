"""deep research agent —— 长时序 + context 工程 + planning。

相比 naive_agent/xagent.py（一个 while loop）、code_agent/graph.py（一张 2 节点图），
新增三个设计，均对应业界 deep research 产品（open_deep_research / 秘塔 AI 搜索 /
问小白等）的真实做法：

1. planning：先用一次 LLM 调用出一份粗 checklist（3-5 个子问题），再靠反思循环边做
   边补，checklist 可在过程中被改写。不做 Generative Agents（斯坦福小镇）那种按小时
   排好全天日程的刚性计划——那是为开放世界仿真设计的，本任务有明确终态（一份报告）。
2. memory：不上向量库，全部用文件当记忆（都落在 output/research/<run_id>/ 下，run_id
   由 run() 生成，多次运行不互相覆盖）——
   - plan.md       粗计划（checklist）
   - sources.jsonl 来源索引（代码保证追加，不依赖模型自己抄链接）
   - notes/*.md    每个子任务的压缩笔记
   三份文件本身就是跨步骤的记忆，report 阶段只需读文件。
3. context 工程（隔离 + 压缩）：参考 open_deep_research 的 supervisor/sub-agent 架构——
   supervisor 的 messages 历史里只有决策过程（think/conduct_research/complete），
   不会被子任务里几十条 web_search 结果撑爆；每个子任务在 run_sub_researcher() 里
   用全新、隔离的 messages 数组独立跑，跑完只把最后一段压缩总结带回 supervisor，
   原始检索结果留在 sources.jsonl。这就是 tool-result clearing 的具体做法，
   不需要额外发一次"请你总结"的 LLM 调用。

sub-agent 的 web_search 底层经 MCP 协议连一个独立的本地 server，而非进程内直接调用，
与 function calling 的区别及取舍见 mcp_server.py / mcp_client.py。

运行前：
    cp .env.example .env   # 填好模型和 BOCHA_API_KEY；程序会自动加载

用法：
    python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt
    python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt --model deepseek/deepseek-v4-pro
    python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt --skill   # report 阶段挂 list_skills/load_skill
    # 轮数上限可调（默认 supervisor=12 / sub-agent=8 / report=10）：
    python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt --supervisor-max-turns 6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from deep_research_agent import tools as dr_tools
from deep_research_agent.tools import (
    DISPATCH,
    DISPATCH_NO_SKILLS,
    TOOLS,
    TOOLS_NO_SKILLS,
    write_file,
)
from lib.llm import chat

HERE = os.path.dirname(os.path.abspath(__file__))
# 默认轮数上限；CLI / run() 可覆盖（对照实验用，同 gui_agent 的 --max-steps）。
SUPERVISOR_MAX_TURNS = 12
SUB_AGENT_MAX_TURNS = 8
REPORT_MAX_TURNS = 10

with open(os.path.join(HERE, "RULES.md"), encoding="utf-8") as _f:
    RULES = _f.read()

# 运行期可切换（同 code_agent 的做法：langgraph 节点函数只认 state，运行期配置用模块级变量）。
_model_override: str | None = None
_use_skills = False
_supervisor_max_turns = SUPERVISOR_MAX_TURNS
_sub_agent_max_turns = SUB_AGENT_MAX_TURNS
_report_max_turns = REPORT_MAX_TURNS
# 三份记忆文件（plan.md/notes/sources.jsonl）和最终报告都落在这个目录下，run() 里改成
# output/research/<run_id>/，让多次运行不互相覆盖；plan_node/supervisor_tools_node/
# report writer 都读这个变量而不是写死 "output/research/"。
_output_dir = "output/research"


def _append(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return left + right


class SupervisorState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _append]
    checklist: list[dict[str, str]]  # [{"topic": ..., "status": "pending"|"done"}]
    notes: dict[str, str]
    turn: int


# ---------- 独立、隔离上下文的子循环：跟 naive_agent.xagent.run() 结构完全一样 ----------
# 故意不用 langgraph subgraph/Send 实现——sub-agent 的"隔离"只是它有一个自己独立的、
# 与 supervisor 完全不共享的 messages 数组，一个普通函数调用就能做到，再包一层图
# 没有收益。同 code_agent/graph.py 顶部注释的结论：loop 是核心，图是包装。

SUB_AGENT_SYSTEM = (
    "你是一个研究子任务执行者。只负责回答分配给你的这一个子问题，不用管整体报告的其它部分。"
    "用 web_search 查外部资料，用 read_file 按需查 workspace/研报数据/ 内部资料。"
    "每个事实性论断后面必须标注来源编号，例如 [3]（web_search 结果会告诉你编号）；"
    "workspace 内部数据没有编号，直接写「来自内部研报素材」。"
    "查到足够信息后，直接用一段 300-500 字的话给出压缩后的研究结论作为最终回复，"
    "不要再调用工具——这段话会被原样交给上级，请确保自包含、带编号。"
)


def run_sub_researcher(topic: str, *, model: str | None = None) -> str:
    """跑一个子问题的隔离子循环，返回压缩后的笔记文本（模型的最终自然语言回复）。"""
    messages = [
        {"role": "system", "content": SUB_AGENT_SYSTEM},
        {"role": "user", "content": f"子问题：{topic}"},
    ]
    tools = TOOLS_NO_SKILLS  # 子任务不需要 list_skills：没有专门给检索写的技能
    dispatch = DISPATCH_NO_SKILLS
    for turn in range(1, _sub_agent_max_turns + 1):
        response = chat(messages, tools=tools, model=model, max_tokens=4096)
        msg = response.choices[0].message
        messages.append(msg.model_dump())
        if not msg.tool_calls:
            return msg.content or "(子任务没有产出内容)"
        for call in msg.tool_calls:
            name = call.function.name
            print(f"    [子任务:{topic[:20]}] 调用 {name}({call.function.arguments[:120]})")
            try:
                args = json.loads(call.function.arguments)
                result = dispatch[name](**args)
            except Exception as e:
                result = f"工具执行出错：{type(e).__name__}: {e}"
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": str(result)[:8000],
            })
    return f"(达到子任务最大轮数 {_sub_agent_max_turns}，可能未完全收敛，以下是最后一轮前的内容)\n" + \
        (messages[-2].get("content") or "无")


def _report_writer_system(output_dir: str) -> str:
    return (
        "你是报告撰写者。先用 list_skills/load_skill（如果有）看看有没有 report-writing 技能，"
        f"按技能里的步骤：读 {output_dir}/plan.md、{output_dir}/notes/ 下所有笔记、"
        f"{output_dir}/sources.jsonl，整合成一份完整专题报告，写入 {output_dir}/专题报告.md。"
        "报告必须带引用编号和参考文献列表，覆盖 plan.md 里的每个子问题。"
    )


def run_report_writer(*, model: str | None = None, use_skills: bool = False) -> str:
    """跑报告成文的隔离子循环；写文件本身也是通过 write_file 工具完成，不是直接返回文本。"""
    messages = [
        {"role": "system", "content": _report_writer_system(_output_dir)},
        {"role": "user", "content": "开始整合笔记，写最终报告。"},
    ]
    tools = TOOLS if use_skills else TOOLS_NO_SKILLS
    dispatch = DISPATCH if use_skills else DISPATCH_NO_SKILLS
    final = ""
    for turn in range(1, _report_max_turns + 1):
        response = chat(messages, tools=tools, model=model, max_tokens=8192)
        msg = response.choices[0].message
        messages.append(msg.model_dump())
        if not msg.tool_calls:
            final = msg.content or ""
            break
        for call in msg.tool_calls:
            name = call.function.name
            print(f"  [report writer] 调用 {name}({call.function.arguments[:150]})")
            try:
                args = json.loads(call.function.arguments)
                result = dispatch[name](**args)
            except Exception as e:
                result = f"工具执行出错：{type(e).__name__}: {e}"
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": str(result)[:8000],
            })
    else:
        print(f"  [report writer] 达到最大轮数 {_report_max_turns}，强制停止")
    return final


# ---------- 粗计划：一次 LLM 调用生成 checklist，不是刚性全局日程 ----------

def plan_node(state: SupervisorState) -> dict[str, Any]:
    task = state["messages"][-1]["content"]
    prompt = (
        f"任务：{task}\n\n"
        "先看一眼 workspace/研报数据/sources.md 里已经标注的「已知数据缺口/冲突」（如果任务提到），"
        "再把这个任务拆成 3-5 个需要联网调研才能回答的子问题（checklist），只输出 JSON 数组，"
        '每项是字符串，例如 ["子问题1", "子问题2"]，不要输出其它内容。'
    )
    response = chat(
        [{"role": "system", "content": "你是研究任务的规划者。"}, {"role": "user", "content": prompt}],
        model=_model_override, max_tokens=1024,
    )
    text = response.choices[0].message.content or "[]"
    match = re.search(r"\[.*\]", text, re.S)
    try:
        topics = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        topics = []
    if not topics:
        topics = [task]  # 兜底：规划失败就把整个任务当成唯一子问题，不让流程卡死
    checklist = [{"topic": t, "status": "pending"} for t in topics]

    plan_md = "# 研究计划（checklist，执行中可被 supervisor 反思后修订）\n\n" + \
        "\n".join(f"- [ ] {t}" for t in topics)
    write_file(f"{_output_dir}/plan.md", plan_md)
    print(f"\n[plan] 生成 {len(topics)} 个子问题：{topics}")

    brief = "已生成研究计划，子问题如下：\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics)) + \
        "\n\n你是 supervisor，只负责调度、不亲自查资料：用 conduct_research 逐个委派这些子问题" \
        "（可以先 think 决定顺序/要不要合并/是否有遗漏），每次委派后会拿到压缩后的研究结论。" \
        "所有子问题都有结论后调用 research_complete——报告成文是另一个独立阶段，不需要你在这里写。"
    return {
        "checklist": checklist,
        "messages": [
            {"role": "system", "content": "你是 deep research agent 的 supervisor。以下是你必须遵守的规则：\n\n" + RULES},
            {"role": "user", "content": brief},
        ],
        "turn": 0,
    }


# ---------- supervisor：只做决策，不直接碰检索细节 ----------

SUPERVISOR_TOOLS = [
    {"type": "function", "function": {
        "name": "think",
        "description": "记录一次策略性思考/反思（比如判断 checklist 是否有遗漏），不产生副作用",
        "parameters": {"type": "object", "properties": {
            "reflection": {"type": "string"}}, "required": ["reflection"]}}},
    {"type": "function", "function": {
        "name": "conduct_research",
        "description": "把一个子问题委派给一个独立的研究子任务（有自己隔离的上下文），返回压缩后的研究结论",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "要研究的子问题，尽量具体"}},
            "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "research_complete",
        "description": "确认 checklist 里所有子问题都已经研究完成，进入报告成文阶段",
        "parameters": {"type": "object", "properties": {}}}},
]


def supervisor_node(state: SupervisorState) -> dict[str, Any]:
    response = chat(state["messages"], tools=SUPERVISOR_TOOLS, model=_model_override, max_tokens=4096)
    msg = response.choices[0].message
    return {"messages": [msg.model_dump()], "turn": state["turn"] + 1}


def supervisor_tools_node(state: SupervisorState) -> dict[str, Any]:
    """执行 supervisor 的工具调用：think / conduct_research / research_complete。

    隔离的是过程，不是结论：子任务内部几十条 web_search 原始检索记录留在
    sources.jsonl 和子任务自己的 messages 里，从不进 supervisor 的上下文；但压缩后的
    结论（note，一两百字级别）必须带回 supervisor——否则它拿不到任何实质信息，既判断
    不了 checklist 是否覆盖到位，也没法决定要不要补研究，只能盲目重复委派（实测复现过：
    supervisor 看不到内容时，反复把同一个子问题拆成新的子任务）。这与 open_deep_research
    的做法一致：sub-agent compress 后的结论回传 supervisor。
    """
    last = state["messages"][-1]
    results = []
    checklist = [dict(c) for c in state["checklist"]]
    notes = dict(state["notes"])
    complete = False
    for call in last["tool_calls"]:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        if name == "think":
            content = f"已记录反思：{args.get('reflection', '')[:200]}"
        elif name == "conduct_research":
            topic = args.get("topic", "")
            print(f"\n[supervisor] 委派子任务 -> {topic}")
            note = run_sub_researcher(topic, model=_model_override)
            notes[topic] = note
            slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", topic)[:40]
            write_file(f"{_output_dir}/notes/{slug}.md", f"# {topic}\n\n{note}\n")
            for item in checklist:
                if item["topic"] == topic:
                    item["status"] = "done"
            # 结论截断后回传 supervisor（隔离的是过程不是结论，见本函数 docstring），
            # 截断是防单个子任务的超长笔记把 supervisor 上下文吃满。
            content = f"[已完成] {topic}\n\n{note[:2000]}"
        elif name == "research_complete":
            complete = True
            content = "已确认研究完成，进入报告成文阶段"
        else:
            content = f"未知工具 {name}"
        print(f"[supervisor:{name}] {content[:200]}")
        results.append({"role": "tool", "tool_call_id": call["id"], "content": content})
    return {"messages": results, "checklist": checklist, "notes": notes, "_complete": complete}


def route_after_supervisor(state: SupervisorState) -> str:
    last = state["messages"][-1]
    tool_calls = last.get("tool_calls") if isinstance(last, dict) else None
    if state["turn"] >= _supervisor_max_turns:
        return "report"
    if tool_calls:
        return "tools"
    return "report"  # 模型不调工具也直接进入成文（当成"我认为做完了"）


def route_after_tools(state: SupervisorState) -> str:
    # 用一个瞬时字段判断 research_complete 是否被调用过；langgraph 的 state 会持久化
    # 这个 key，但下一轮 plan/tools 都不会再写它，所以只在这一步读一次就够。
    return "report" if state.get("_complete") else "agent"


def report_node(state: SupervisorState) -> dict[str, Any]:
    print("\n[report] 开始整合笔记，撰写最终报告")
    final = run_report_writer(model=_model_override, use_skills=_use_skills)
    return {"messages": [{"role": "assistant", "content": final}]}


def build_graph():
    graph = StateGraph(SupervisorState)
    graph.add_node("plan", plan_node)
    graph.add_node("agent", supervisor_node)
    graph.add_node("tools", supervisor_tools_node)
    graph.add_node("report", report_node)
    graph.set_entry_point("plan")
    graph.add_edge("plan", "agent")
    graph.add_conditional_edges("agent", route_after_supervisor, {"tools": "tools", "report": "report"})
    graph.add_conditional_edges("tools", route_after_tools, {"agent": "agent", "report": "report"})
    graph.add_edge("report", END)
    return graph.compile(checkpointer=MemorySaver())


def run(
    task: str,
    thread_id: str = "default",
    *,
    model: str | None = None,
    use_skills: bool = False,
    supervisor_max_turns: int = SUPERVISOR_MAX_TURNS,
    sub_agent_max_turns: int = SUB_AGENT_MAX_TURNS,
    report_max_turns: int = REPORT_MAX_TURNS,
) -> str:
    global _model_override, _use_skills, _output_dir
    global _supervisor_max_turns, _sub_agent_max_turns, _report_max_turns
    _model_override = model
    _use_skills = use_skills
    _supervisor_max_turns = supervisor_max_turns
    _sub_agent_max_turns = sub_agent_max_turns
    _report_max_turns = report_max_turns
    run_id = time.strftime("%Y%m%d_%H%M%S")
    _output_dir = f"output/research/{run_id}"
    dr_tools.SOURCES_PATH = os.path.join(dr_tools.WORKSPACE, _output_dir, "sources.jsonl")

    app = build_graph()
    state: SupervisorState = {
        "messages": [{"role": "user", "content": task}],
        "checklist": [],
        "notes": {},
        "turn": 0,
    }
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
    final_content = ""
    for update in app.stream(state, config=config, stream_mode="updates"):
        for node, delta in update.items():
            if node == "report" and delta.get("messages"):
                final_content = delta["messages"][-1]["content"]
    print(f"\n[deep research agent 最终回复]\n{final_content}")
    return final_content


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="deep research agent（LangGraph 多智能体版）")
    parser.add_argument("--prompt", help="任务描述，或写着任务描述的文件路径")
    parser.add_argument("--model", help="覆盖 .env 里的 MODEL，例如 deepseek/deepseek-v4-pro")
    parser.add_argument("--skill", action="store_true",
                         help="report 阶段挂载 list_skills/load_skill；默认不挂")
    parser.add_argument("--supervisor-max-turns", type=int, default=SUPERVISOR_MAX_TURNS,
                         help=f"supervisor 决策轮数上限（默认 {SUPERVISOR_MAX_TURNS}）")
    parser.add_argument("--sub-agent-max-turns", type=int, default=SUB_AGENT_MAX_TURNS,
                         help=f"单个子研究员工具循环轮数上限（默认 {SUB_AGENT_MAX_TURNS}）")
    parser.add_argument("--report-max-turns", type=int, default=REPORT_MAX_TURNS,
                         help=f"报告成文工具循环轮数上限（默认 {REPORT_MAX_TURNS}）")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    task = args.prompt or "对 workspace/研报数据/ 里的《toB/toG云服务市场分析》做一份带外部调研的完整专题报告"
    if os.path.isfile(task):
        task = open(task, encoding="utf-8").read()
    run(
        task,
        model=args.model,
        use_skills=args.skill,
        supervisor_max_turns=args.supervisor_max_turns,
        sub_agent_max_turns=args.sub_agent_max_turns,
        report_max_turns=args.report_max_turns,
    )
