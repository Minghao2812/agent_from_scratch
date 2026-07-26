"""code_agent.tools 单测：run_shell 的越界过滤 + 环境变量隔离。

补一个此前完全缺失的覆盖缺口——naive_agent/deep_research_agent 的 tools.py 都有
对应的 run_shell 回归测试，code_agent/tools.py 内容几乎一样却一条都没有，三份
重复代码里出的同一个安全补丁只有两份被回归测试锁住过。
"""

from __future__ import annotations

import os

import pytest

from code_agent.tools import DISPATCH, WORKSPACE, _safe_path

run_shell = DISPATCH["run_shell"]


def test_run_shell_rejects_path_escape():
    """回归测试：明显的越界路径特征（相对路径 `../`、`~/`）必须被拒绝。"""
    assert "拒绝执行" in run_shell("cat ../.env")
    assert "拒绝执行" in run_shell("cat ~/.ssh/id_rsa")
    assert "拒绝执行" not in run_shell("echo hello")


def test_run_shell_rejects_bare_dotdot_and_absolute_path():
    """回归测试：`../` 子串匹配堵不住裸 `..`（`cd ..`）和任意绝对路径，两处都在
    naive_agent/tools.py 上实测复现过（见 PROGRESS.md），这里锁住 code_agent 同款
    修复没有漏做。"""
    assert "拒绝执行" in run_shell("cd .. && cat .env")
    assert "拒绝执行" in run_shell("cat /Users/anyone/project/.env")
    assert "拒绝执行" not in run_shell("curl -s http://example.com")


def test_run_shell_allows_absolute_path_that_resolves_inside_workspace():
    """回归测试：绝对路径不能一律拒绝——模型 `pwd` 后原样 `cd` 回去这种无害操作
    之前被误伤过，见 naive_agent 同名测试和 PROGRESS.md。"""
    assert "拒绝执行" not in run_shell(f"cd {WORKSPACE} && echo ok")
    sibling = os.path.join(os.path.dirname(WORKSPACE), "lib")
    assert "拒绝执行" in run_shell(f"ls {sibling}")


def test_run_shell_does_not_leak_secret_env_vars(monkeypatch):
    """回归测试：run_shell 默认继承父进程环境，`echo $KEY`/`env` 不碰文件路径就能
    把 .env 里的 provider key 读出来，路径过滤堵不住这条泄露路径。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    assert "sk-should-not-leak" not in run_shell("echo $DEEPSEEK_API_KEY")
    assert "sk-should-not-leak" not in run_shell("env")
    assert run_shell("echo $PATH").strip() != ""


def test_run_shell_dispatches_normally():
    result = run_shell("echo hi")
    assert "hi" in result


def test_safe_path_strips_redundant_workspace_prefix():
    """回归测试：模型把沙箱根目录名 "workspace" 误当路径前缀再写一遍，会造出
    workspace/workspace/ 嵌套目录（真实复现，见 naive_agent 同名测试和 PROGRESS.md）。"""
    normal = _safe_path("banner.py")
    assert _safe_path("workspace/banner.py") == normal
    assert _safe_path("workspace/workspace/banner.py") == normal
    assert _safe_path("output/workspace/foo.txt") == os.path.join(WORKSPACE, "output", "workspace", "foo.txt")


def test_safe_path_still_rejects_escape_after_stripping_prefix():
    with pytest.raises(ValueError):
        _safe_path("workspace/../../etc/passwd")
