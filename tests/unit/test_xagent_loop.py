"""xagent 主循环的边界测试：全部 mock 掉 lib.llm.chat，不打真实 API，跑得快、可重复。

覆盖三个停止条件 + 一个健壮性回归（tool_call 参数 JSON 截断不能崩掉整个进程，
这是真实跑 naive_agent 时抓到的 bug，见 PROGRESS.md 2026-07-25）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from naive_agent import xagent
from naive_agent.tools import DISPATCH, WORKSPACE, _safe_path

run_shell = DISPATCH["run_shell"]


def _msg(content=None, tool_calls=None):
    """构造一个和 litellm ModelResponse.choices[0].message 形状一致的假消息。"""
    m = SimpleNamespace(content=content, tool_calls=tool_calls)
    m.model_dump = lambda: {"role": "assistant", "content": content, "tool_calls": tool_calls}
    return m


def _resp(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def test_stops_when_model_returns_no_tool_calls(capsys):
    """停止条件一：模型不调工具就直接结束，不会继续循环。"""
    with patch.object(xagent, "chat", return_value=_resp(_msg(content="任务完成"))) as mock_chat:
        xagent.run("随便一个任务")
    assert mock_chat.call_count == 1
    assert "任务完成" in capsys.readouterr().out


def test_tool_error_is_fed_back_not_raised(capsys):
    """停止条件二：工具执行报错（比如路径越界）也是 observation，不会让 run() 抛异常。"""
    call = _tool_call("c1", "read_file", json.dumps({"path": "../../etc/passwd"}))
    responses = [
        _resp(_msg(tool_calls=[call])),
        _resp(_msg(content="看到工具报错了，我停下来")),
    ]
    with patch.object(xagent, "chat", side_effect=responses):
        xagent.run("读一个越界文件")  # 不应该抛异常
    out = capsys.readouterr().out
    assert "越界" in out or "出错" in out


def test_malformed_tool_call_arguments_do_not_crash_process(capsys):
    """回归测试：tool_call.arguments 是截断的非法 JSON 时，不能让整个 run() 崩掉。"""
    bad_call = _tool_call("c1", "run_shell", '{"command": "echo hi')  # 故意截断，非法 JSON
    responses = [
        _resp(_msg(tool_calls=[bad_call])),
        _resp(_msg(content="参数解析失败，我看到了错误提示，停止")),
    ]
    with patch.object(xagent, "chat", side_effect=responses):
        xagent.run("触发一次参数截断")  # 关键断言：不抛异常
    out = capsys.readouterr().out
    assert "出错" in out


def test_run_shell_rejects_path_escape():
    """回归测试：真实跑 prompt injection demo 时发现 run_shell 完全没做路径限制，
    模型被注入的工单诱导执行 `cat ../.env` 把 API key 导出到 workspace/output/
    （见 PROGRESS.md）。这里锁住修复后的行为：明显的越界路径特征必须被拒绝。
    """
    assert "拒绝执行" in run_shell("cat ../.env")
    assert "拒绝执行" in run_shell("cat ~/.ssh/id_rsa")
    assert "拒绝执行" not in run_shell("echo hello")


def test_run_shell_rejects_bare_dotdot_without_trailing_slash():
    """回归测试：`../` 子串匹配堵不住裸 `..`——`cd ..` 不带斜杠也能跳出 workspace
    上一级再读 `.env`，代码审查时用真实 .env 复现过（见 PROGRESS.md）。"""
    assert "拒绝执行" in run_shell("cd .. && cat .env")
    assert "拒绝执行" in run_shell("cd ..")
    assert "拒绝执行" not in run_shell("echo a..b")  # 文件名里的 .. 不应被误伤


def test_run_shell_rejects_arbitrary_absolute_path():
    """回归测试：原黑名单只挡了 /etc/、/root/ 两个目录，任意其它绝对路径
    （比如项目根目录真实 .env 的绝对路径）完全不受限制，实测复现过。"""
    assert "拒绝执行" in run_shell("cat /Users/anyone/some/project/.env")
    assert "拒绝执行" not in run_shell("curl -s http://example.com")  # URL 不应被误伤


def test_run_shell_allows_absolute_path_that_resolves_inside_workspace():
    """回归测试：模型经常先 `pwd` 确认当前目录，再原样 `cd` 回这个绝对路径——之前
    "见绝对路径就拒"把这种无害操作也拒了，逼模型多绕好几轮才找到能跑通的写法
    （真实复现，见 PROGRESS.md）。只有解析后落在 workspace 之外的绝对路径才该拒绝。
    """
    import os

    assert "拒绝执行" not in run_shell(f"cd {WORKSPACE} && echo ok")
    assert "拒绝执行" not in run_shell(f"ls {WORKSPACE}/output")
    # 绝对路径指向 workspace 的父目录（不含字面 "../"，专门测新函数自己的越界判断，
    # 不是靠 _ESCAPE_PATTERNS 的子串匹配碰巧挡住）：project 根目录下真实存在的 lib/。
    sibling = os.path.join(os.path.dirname(WORKSPACE), "lib")
    assert "拒绝执行" in run_shell(f"ls {sibling}")


def test_run_shell_does_not_leak_secret_env_vars(monkeypatch):
    """回归测试：run_shell 之前默认继承整个父进程环境，`.env` 里的 provider key 全部
    在 os.environ 里，`echo $KEY` / `env` 不需要碰任何文件路径就能把 key 读出来，
    路径过滤对这条泄露路径完全无效（比路径穿越更严重，实测复现过）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    assert "sk-should-not-leak" not in run_shell("echo $DEEPSEEK_API_KEY")
    assert "sk-should-not-leak" not in run_shell("env")
    # 非密钥变量应该正常传给子进程，不能矫枉过正把 PATH 之类也过滤掉
    assert run_shell("echo $PATH").strip() != ""


def test_safe_path_strips_redundant_workspace_prefix():
    """回归测试：模型把沙箱根目录名 "workspace" 误当路径前缀再写一遍，之前会造出
    workspace/workspace/ 嵌套目录（真实复现：见本次对话记录和 PROGRESS.md）。
    _safe_path 现在应剥离开头所有多余的 workspace 前缀。"""
    import os

    normal = _safe_path("banner.py")
    assert _safe_path("workspace/banner.py") == normal
    assert _safe_path("workspace/workspace/banner.py") == normal  # 重复误加也要剥干净
    # 不属于这个前缀陷阱的正常路径不应被误伤
    assert _safe_path("output/workspace/foo.txt") == os.path.join(WORKSPACE, "output", "workspace", "foo.txt")


def test_safe_path_still_rejects_escape_after_stripping_prefix():
    """剥离前缀不能削弱越界检测：带了 workspace/ 前缀的越界路径依然要被拒绝。"""
    import pytest

    with pytest.raises(ValueError):
        _safe_path("workspace/../../etc/passwd")


def test_max_turns_forces_stop(capsys):
    """停止条件三：模型一直调工具、不停，达到 max_turns 后必须强制停止。"""
    call = _tool_call("c1", "run_shell", json.dumps({"command": "echo loop"}))
    with patch.object(xagent, "chat", return_value=_resp(_msg(tool_calls=[call]))) as mock_chat:
        xagent.run("一个永远不停止的任务", max_turns=3)
    assert mock_chat.call_count == 3
    assert "达到最大轮数 3" in capsys.readouterr().out


def test_default_run_does_not_expose_skill_tools():
    """默认不挂 skills；只有 use_skills=True（CLI --skill）才把 list_skills 传给模型。"""
    with patch.object(xagent, "chat", return_value=_resp(_msg(content="ok"))) as mock_chat:
        xagent.run("无需技能")
    tools = mock_chat.call_args.kwargs["tools"]
    names = {t["function"]["name"] for t in tools}
    assert "list_skills" not in names
    assert "load_skill" not in names

    with patch.object(xagent, "chat", return_value=_resp(_msg(content="ok"))) as mock_chat:
        xagent.run("需要技能", use_skills=True)
    tools = mock_chat.call_args.kwargs["tools"]
    names = {t["function"]["name"] for t in tools}
    assert {"list_skills", "load_skill"} <= names


def test_list_skills_reads_frontmatter_description():
    """技能规范：list_skills 应展示 frontmatter description，而不是 markdown 标题。"""
    from naive_agent.tools import list_skills

    out = list_skills()
    assert "enterprise-analysis:" in out
    assert "研报" in out or "分析" in out
    assert "# 企业" not in out
