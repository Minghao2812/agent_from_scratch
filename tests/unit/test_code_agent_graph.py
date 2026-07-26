"""code_agent StateGraph 的节点级单测：mock lib.llm.chat，不打真实 API。

只测节点函数本身（agent_node / tools_node / route_after_agent），不跑整张编译好的
图——图的编排是 langgraph 自己的职责，这里只需确认「换成图之后，loop 本身的语义
（工具报错不崩、达到 max_turns 会停）没有变」。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.graph import END

from code_agent import graph as code_graph


def _msg(content=None, tool_calls=None):
    return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def _mock_response(message_dict):
    msg = SimpleNamespace(**message_dict)
    msg.model_dump = lambda: message_dict
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_agent_node_increments_turn_and_appends_message():
    state = {"messages": [{"role": "user", "content": "hi"}], "turn": 0}
    with patch.object(code_graph, "chat", return_value=_mock_response(_msg(content="done"))):
        delta = code_graph.agent_node(state)
    assert delta["turn"] == 1
    assert delta["messages"][0]["content"] == "done"


def test_route_after_agent_ends_without_tool_calls():
    state = {"messages": [_msg(content="done")], "turn": 1}
    assert code_graph.route_after_agent(state) == END


def test_route_after_agent_goes_to_tools_when_tool_calls_present():
    call = {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
    state = {"messages": [_msg(tool_calls=[call])], "turn": 1}
    assert code_graph.route_after_agent(state) == "tools"


def test_route_after_agent_forces_end_at_max_turns():
    call = {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
    state = {"messages": [_msg(tool_calls=[call])], "turn": code_graph._max_turns}
    assert code_graph.route_after_agent(state) == END


def test_tools_node_malformed_arguments_do_not_raise():
    """回归测试：tool_call 参数截断（naive_agent 遇到过的真实 bug）在图里也不能崩。"""
    call = {"id": "c1", "function": {"name": "run_shell", "arguments": '{"command": "echo hi'}}
    state = {"messages": [_msg(tool_calls=[call])], "turn": 1}
    delta = code_graph.tools_node(state)  # 不应该抛异常
    assert "出错" in delta["messages"][0]["content"]


def test_tools_node_dispatches_and_returns_tool_message():
    call = {"id": "c2", "function": {"name": "run_shell", "arguments": json.dumps({"command": "echo hi"})}}
    state = {"messages": [_msg(tool_calls=[call])], "turn": 1}
    delta = code_graph.tools_node(state)
    assert delta["messages"][0]["role"] == "tool"
    assert delta["messages"][0]["tool_call_id"] == "c2"
    assert "hi" in delta["messages"][0]["content"]


def test_default_run_does_not_expose_skill_tools():
    """默认不挂 skills；只有 use_skills=True（CLI --skill）才把 list_skills 传给模型。"""
    with patch.object(code_graph, "build_graph") as mock_build:
        mock_build.return_value.stream.return_value = []
        code_graph.run("无需技能")
    names = {t["function"]["name"] for t in code_graph._active_tools}
    assert "list_skills" not in names
    assert "load_skill" not in names

    with patch.object(code_graph, "build_graph") as mock_build:
        mock_build.return_value.stream.return_value = []
        code_graph.run("需要技能", use_skills=True)
    names = {t["function"]["name"] for t in code_graph._active_tools}
    assert {"list_skills", "load_skill"} <= names


def test_list_skills_reads_frontmatter_description():
    """技能规范：list_skills 应展示 frontmatter description，而不是 markdown 标题。"""
    from code_agent.tools import list_skills

    out = list_skills()
    assert "dashboard:" in out
    assert "Plotly" in out or "看板" in out
    assert "# 用 Plotly" not in out
