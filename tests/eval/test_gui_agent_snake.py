"""gui_agent 操作贪吃蛇的产出物验收：至少吃到 1 次食物。

跟另外三个 agent 的 eval 测试同一个模式——先用 CLI 实际跑一次（真实浏览器 + 真实
VLM），把结果落盘成 workspace/output/gui/<run_id>/snake_result.json，测试只读最近
一次运行的持久化结果，不在 pytest 里重新触发一次真实调用（真实调用几十秒、非确定性，
不适合每次 CI 都跑）：

    python -m gui_agent.graph --max-steps 60

多次运行的产物按时间戳分开存放（不互相覆盖），这里只看最新一次。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import latest_run_dir

ROOT = Path(__file__).resolve().parents[2]
GUI_OUTPUT_ROOT = ROOT / "workspace" / "output" / "gui"


@pytest.fixture(scope="module")
def result() -> dict:
    run_dir = latest_run_dir(GUI_OUTPUT_ROOT)
    if run_dir is None:
        pytest.skip(f"产出物不存在：{GUI_OUTPUT_ROOT} 下没有任何一次运行，先跑 python -m gui_agent.graph")
    result_file = run_dir / "snake_result.json"
    if not result_file.exists():
        pytest.skip(f"产出物不存在：{result_file}")
    return json.loads(result_file.read_text(encoding="utf-8"))


def test_ran_at_least_one_step(result):
    assert result["steps"] >= 1


def test_ate_at_least_one_food(result):
    """核心验收指标：score >= 1，证明 agent 真的看懂了截图并做出了正确方向判断，
    不是靠默认方向（蛇初始朝右）撞墙走完流程。"""
    assert result["score"] >= 1
