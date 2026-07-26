"""deep_research_agent 节点级单测：mock lib.llm.chat 和文件/MCP 副作用，不打真实 API/网络。

覆盖三层：
1. plan_node：checklist 生成 + JSON 解析失败时的兜底。
2. supervisor 的路由：有没有 tool_calls / turn 是否到顶 / research_complete 是否触发。
3. supervisor_tools_node：think 无副作用、conduct_research 委派并只把"确认信息"带回
   supervisor（不把整段笔记塞回去——这是"隔离"的关键行为，必须锁住）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.graph import END

from deep_research_agent import graph as dr_graph


def _msg(content=None, tool_calls=None):
    m = SimpleNamespace(content=content, tool_calls=tool_calls)
    m.model_dump = lambda: {"role": "assistant", "content": content, "tool_calls": tool_calls}
    return m


def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


# ---------- plan_node ----------

def test_plan_node_parses_checklist_and_seeds_messages():
    state = {"messages": [{"role": "user", "content": "写一份专题报告"}], "checklist": [], "notes": {}, "turn": 0}
    with patch.object(dr_graph, "chat", return_value=_resp(_msg(content='["子问题A", "子问题B"]'))), \
         patch.object(dr_graph, "write_file") as mock_write:
        delta = dr_graph.plan_node(state)
    assert [c["topic"] for c in delta["checklist"]] == ["子问题A", "子问题B"]
    assert all(c["status"] == "pending" for c in delta["checklist"])
    assert delta["turn"] == 0
    assert delta["messages"][0]["role"] == "system"
    assert "子问题A" in delta["messages"][1]["content"]
    mock_write.assert_called_once()
    assert mock_write.call_args[0][0] == "output/research/plan.md"


def test_plan_node_falls_back_to_whole_task_when_json_invalid():
    """规划失败（模型没按格式输出）不能让流程卡死，退化成把整个任务当唯一子问题。"""
    state = {"messages": [{"role": "user", "content": "任务描述"}], "checklist": [], "notes": {}, "turn": 0}
    with patch.object(dr_graph, "chat", return_value=_resp(_msg(content="我不知道怎么拆"))), \
         patch.object(dr_graph, "write_file"):
        delta = dr_graph.plan_node(state)
    assert len(delta["checklist"]) == 1
    assert delta["checklist"][0]["topic"] == "任务描述"


# ---------- 路由 ----------

def test_route_after_supervisor_goes_to_tools_when_tool_calls_present():
    call = {"id": "c1", "function": {"name": "think", "arguments": "{}"}}
    state = {"messages": [_msg(tool_calls=[call]).model_dump()], "turn": 1}
    assert dr_graph.route_after_supervisor(state) == "tools"


def test_route_after_supervisor_goes_to_report_without_tool_calls():
    state = {"messages": [_msg(content="done").model_dump()], "turn": 1}
    assert dr_graph.route_after_supervisor(state) == "report"


def test_route_after_supervisor_forces_report_at_max_turns():
    call = {"id": "c1", "function": {"name": "think", "arguments": "{}"}}
    state = {"messages": [_msg(tool_calls=[call]).model_dump()], "turn": dr_graph._supervisor_max_turns}
    assert dr_graph.route_after_supervisor(state) == "report"


def test_route_after_tools_respects_complete_flag():
    assert dr_graph.route_after_tools({"_complete": True}) == "report"
    assert dr_graph.route_after_tools({"_complete": False}) == "agent"
    assert dr_graph.route_after_tools({}) == "agent"


# ---------- supervisor_tools_node ----------

def _state_with_call(name, arguments, checklist=None):
    call = {"id": "c1", "function": {"name": name, "arguments": arguments}}
    return {
        "messages": [_msg(tool_calls=[call]).model_dump()],
        "checklist": checklist or [{"topic": "子问题A", "status": "pending"}],
        "notes": {},
        "turn": 1,
    }


def test_think_tool_has_no_side_effect_on_checklist_or_notes():
    state = _state_with_call("think", json.dumps({"reflection": "先查政策再查竞对"}))
    delta = dr_graph.supervisor_tools_node(state)
    assert delta["checklist"] == state["checklist"]
    assert delta["notes"] == {}
    assert not delta["_complete"]


def test_conduct_research_delegates_and_returns_compressed_note_to_supervisor():
    """关键行为：子任务内部的原始检索过程不进 supervisor 上下文（隔离的是过程），
    但压缩后的结论（note）要带回 supervisor，否则它看不到任何实质信息，只能盲目重复
    委派同一个子问题（真实复现过，见 PROGRESS.md）。超长笔记要截断，不能无限带回。
    """
    note = "压缩后的研究结论 [1][2]"
    state = _state_with_call("conduct_research", json.dumps({"topic": "子问题A"}))
    with patch.object(dr_graph, "run_sub_researcher", return_value=note) as mock_sub, \
         patch.object(dr_graph, "write_file") as mock_write:
        delta = dr_graph.supervisor_tools_node(state)
    mock_sub.assert_called_once_with("子问题A", model=None)
    assert delta["notes"]["子问题A"] == note
    assert delta["checklist"][0]["status"] == "done"
    tool_message_content = delta["messages"][0]["content"]
    assert note in tool_message_content, "supervisor 应该拿到压缩后的结论，而不是只有一句确认"
    assert "子问题A" in tool_message_content
    mock_write.assert_called_once()
    assert mock_write.call_args[0][0].startswith("output/research/notes/")


def test_conduct_research_truncates_overlong_note_before_returning_to_supervisor():
    """超长笔记（模型没按 300-500 字要求收敛）截断到 2000 字符，避免吃满 supervisor 上下文。"""
    long_note = "很长的研究结论。" * 500
    state = _state_with_call("conduct_research", json.dumps({"topic": "子问题A"}))
    with patch.object(dr_graph, "run_sub_researcher", return_value=long_note), \
         patch.object(dr_graph, "write_file"):
        delta = dr_graph.supervisor_tools_node(state)
    tool_message_content = delta["messages"][0]["content"]
    assert len(tool_message_content) < len(long_note)
    assert delta["notes"]["子问题A"] == long_note, "落盘/state 里保留完整笔记，截断只发生在回传 supervisor 的这条消息里"


def test_research_complete_sets_complete_flag():
    state = _state_with_call("research_complete", "{}")
    delta = dr_graph.supervisor_tools_node(state)
    assert delta["_complete"] is True


def test_supervisor_tools_node_malformed_arguments_do_not_raise():
    """回归防御：跟 naive/code_agent 一样，tool_call 参数解析失败不能崩掉整个图。"""
    state = _state_with_call("conduct_research", '{"topic": "截断了')  # 非法 JSON
    with patch.object(dr_graph, "run_sub_researcher", return_value="note") as mock_sub:
        delta = dr_graph.supervisor_tools_node(state)  # 不应该抛异常
    mock_sub.assert_called_once_with("", model=None)
    assert delta["messages"][0]["role"] == "tool"


# ---------- run_sub_researcher（隔离子循环本身） ----------

def test_run_sub_researcher_stops_when_no_tool_calls():
    with patch.object(dr_graph, "chat", return_value=_resp(_msg(content="压缩后的结论 [1]"))) as mock_chat:
        note = dr_graph.run_sub_researcher("某子问题")
    assert note == "压缩后的结论 [1]"
    assert mock_chat.call_count == 1


def test_run_sub_researcher_executes_tools_before_final_answer():
    call = _tool_call("c1", "web_search", json.dumps({"query": "测试查询"}))
    responses = [
        _resp(_msg(tool_calls=[call])),
        _resp(_msg(content="基于检索结果的结论 [1]")),
    ]
    with patch.object(dr_graph, "chat", side_effect=responses), \
         patch.dict(dr_graph.DISPATCH_NO_SKILLS, {"web_search": lambda query: "[1] mock result"}):
        note = dr_graph.run_sub_researcher("某子问题")
    assert note == "基于检索结果的结论 [1]"


def test_run_sub_researcher_forces_stop_at_max_turns():
    call = _tool_call("c1", "web_search", json.dumps({"query": "无限循环"}))
    dr_graph._sub_agent_max_turns = 3
    try:
        with patch.object(dr_graph, "chat", return_value=_resp(_msg(tool_calls=[call]))) as mock_chat, \
             patch.dict(dr_graph.DISPATCH_NO_SKILLS, {"web_search": lambda query: "mock result"}):
            note = dr_graph.run_sub_researcher("某子问题")
        assert mock_chat.call_count == 3
        assert "达到子任务最大轮数 3" in note
    finally:
        dr_graph._sub_agent_max_turns = dr_graph.SUB_AGENT_MAX_TURNS
