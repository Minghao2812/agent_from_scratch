"""gui_agent 的机制级测试：mock lib.llm.chat（VLM 决策），真实 Playwright 驱动
naive_agent 产出的 snake.html——跟 webdev-arena/cdp_verifier 的 Mock LLM 策略一致
（PITFALLS.md #7）：把 mock 边界画在 LLM 调用处，其余链路（CDP 虚拟时间、按键派发、
截图）全部真实执行，这样才能验证"框架换血之后，浏览器控制这部分有没有真的还能跑"。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gui_agent import graph as gui_graph


def _mock_chat(actions):
    """按顺序返回固定动作序列，序列用完后原地 wait。"""
    seq = iter(actions)

    def _call(*_args, **_kwargs):
        action = next(seq, "wait")
        content = f'{{"thinking": "mock", "action": "{action}"}}'
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return _call


@pytest.fixture
def snake_html():
    path = gui_graph.default_snake_html()
    if not path.is_file():
        pytest.skip(f"产出物不存在：{path}（先跑 naive_agent 产出 snake.html）")
    return path


@pytest.fixture
def mock_summary():
    """绕过真实 summarize_html（单测不打 API）；tick 仍由源码正则在真实 summarize 里解析，
    这里直接给一份最小摘要，budget 走默认 150*2。"""
    return {
        "app_name": "贪吃蛇",
        "control_method": "方向键",
        "elements": {"snake_head": "绿", "snake_body": "绿", "food": "红"},
        "auto_start": True,
        "tick_ms": 150,
        "failure_criteria": "撞墙或撞自己",
        "game_over_signal": "游戏结束遮罩",
    }


def test_parse_action_handles_plain_and_wrapped_json():
    assert gui_graph._parse_action('{"action": "ArrowUp"}') == "ArrowUp"
    assert gui_graph._parse_action('说明：\n```json\n{"action": "stop"}\n```') == "stop"
    assert gui_graph._parse_action('{"action": "continue"}') == "wait"


def test_parse_action_falls_back_to_wait_on_garbage():
    assert gui_graph._parse_action("不是 JSON") == "wait"
    assert gui_graph._parse_action("") == "wait"


def test_tick_ms_from_source_reads_constant_and_settimeout():
    assert gui_graph._tick_ms_from_source("const TICK_MS = 180;") == 180
    assert gui_graph._tick_ms_from_source("setTimeout(tick, 120)") == 120
    assert gui_graph._tick_ms_from_source("no timers here") is None


def test_build_system_prompt_includes_code_summary_not_hardcoded_colors():
    summary = {"elements": {"food": "紫色圆点"}, "tick_ms": 200}
    prompt = gui_graph._build_system_prompt(None, summary)
    assert "紫色圆点" in prompt
    assert "页面背景信息" in prompt


def test_run_drives_real_browser_with_mocked_vlm(tmp_path, snake_html, mock_summary):
    """5 步固定动作序列，跑真实浏览器：验证虚拟时间推进、按键派发、截图三件事真的在工作。
    output_dir 指向 tmp_path，不写 workspace/output/gui——那个目录是真实 CLI 跑
    出来的验收产出物，被单测覆盖会污染 tests/eval 的验收结果（真实踩过，见 PROGRESS.md）。
    """
    with patch.object(gui_graph, "summarize_html", return_value=mock_summary), \
         patch.object(gui_graph, "chat", _mock_chat(["ArrowDown", "wait", "wait", "wait", "stop"])):
        result = gui_graph.run(
            snake_html, "test", max_steps=10, model="mock/mock", headless=True, output_dir=tmp_path,
        )

    assert result["steps"] == 5  # 第 5 步收到 stop，立即结束
    assert result["last_action"] == "stop"
    assert result["score"] >= 0
    assert result["budget_ms"] == 300  # 150 * 2
    shots = list((tmp_path / result["run_id"] / "screenshots").glob("s*.png"))
    assert len(shots) >= 5  # s0 + 4 次 act 截图（stop 那一步不再截图）


def test_run_stops_at_max_steps_without_explicit_stop(tmp_path, snake_html, mock_summary):
    with patch.object(gui_graph, "summarize_html", return_value=mock_summary), \
         patch.object(gui_graph, "chat", _mock_chat(["wait"] * 20)):
        result = gui_graph.run(snake_html, "test", max_steps=3, headless=True, output_dir=tmp_path)
    assert result["steps"] == 3
    assert result["last_action"] == "wait"
