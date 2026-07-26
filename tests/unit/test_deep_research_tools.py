"""deep_research_agent.tools 单测：来源索引追加、web_search 包装、路径安全。"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from deep_research_agent import tools as dr_tools


def test_append_sources_assigns_incrementing_ids(tmp_path):
    sources_path = tmp_path / "sources.jsonl"
    with patch.object(dr_tools, "SOURCES_PATH", str(sources_path)):
        r1 = dr_tools._append_sources("查询1", [{"title": "A", "url": "http://a", "snippet": "sa"}])
        r2 = dr_tools._append_sources("查询2", [{"title": "B", "url": "http://b", "snippet": "sb"}])
    assert r1[0]["id"] == 1
    assert r2[0]["id"] == 2
    lines = sources_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["url"] == "http://a"


def test_web_search_returns_numbered_results_and_records_sources(tmp_path):
    sources_path = tmp_path / "sources.jsonl"
    fake_results = json.dumps([{"title": "T1", "url": "http://u1", "snippet": "s1"}])
    with patch.object(dr_tools, "SOURCES_PATH", str(sources_path)), \
         patch.object(dr_tools, "web_search_via_mcp", return_value=fake_results) as mock_mcp:
        out = dr_tools.web_search("云计算市场")
    mock_mcp.assert_called_once_with("云计算市场", count=5)
    assert "[1] T1" in out
    assert "http://u1" in out


def test_web_search_surfaces_mcp_error_without_crashing():
    with patch.object(dr_tools, "web_search_via_mcp", return_value=json.dumps({"error": "未配置 key"})):
        out = dr_tools.web_search("query")
    assert "检索失败" in out


def test_web_search_handles_non_json_response_gracefully():
    with patch.object(dr_tools, "web_search_via_mcp", return_value="not json"):
        out = dr_tools.web_search("query")
    assert "检索失败" in out


def test_list_skills_reads_report_writing_frontmatter():
    out = dr_tools.list_skills()
    assert "report-writing:" in out
    assert "引用" in out or "报告" in out


def test_run_shell_rejects_path_escape():
    assert "拒绝执行" in dr_tools.run_shell("cat ../.env")
    assert "拒绝执行" not in dr_tools.run_shell("echo hello")


def test_run_shell_rejects_bare_dotdot_and_absolute_path():
    """回归测试：子串匹配堵不住裸 `..`（`cd ..`）和任意绝对路径，两处都实测复现过
    （详见 naive_agent/tools.py 同名测试的注释和 PROGRESS.md）。"""
    assert "拒绝执行" in dr_tools.run_shell("cd .. && cat .env")
    assert "拒绝执行" in dr_tools.run_shell("cat /Users/anyone/project/.env")
    assert "拒绝执行" not in dr_tools.run_shell("curl -s http://example.com")


def test_run_shell_allows_absolute_path_that_resolves_inside_workspace():
    """回归测试：绝对路径不能一律拒绝——模型 `pwd` 后原样 `cd` 回去这种无害操作
    之前被误伤过，见 naive_agent 同名测试和 PROGRESS.md。"""
    assert "拒绝执行" not in dr_tools.run_shell(f"cd {dr_tools.WORKSPACE} && echo ok")
    sibling = os.path.join(os.path.dirname(dr_tools.WORKSPACE), "lib")
    assert "拒绝执行" in dr_tools.run_shell(f"ls {sibling}")


def test_run_shell_does_not_leak_secret_env_vars(monkeypatch):
    """回归测试：run_shell 默认继承父进程环境，之前 `echo $KEY`/`env` 不碰文件路径
    就能把 .env 里的 provider key 读出来，路径过滤堵不住这条泄露路径。"""
    monkeypatch.setenv("BOCHA_API_KEY", "sk-should-not-leak")
    assert "sk-should-not-leak" not in dr_tools.run_shell("echo $BOCHA_API_KEY")
    assert "sk-should-not-leak" not in dr_tools.run_shell("env")


def test_safe_path_strips_redundant_workspace_prefix():
    """回归测试：模型把沙箱根目录名 "workspace" 误当路径前缀再写一遍，会造出
    workspace/workspace/ 嵌套目录（真实复现，见 naive_agent 同名测试和 PROGRESS.md）。"""
    normal = dr_tools._safe_path("banner.py")
    assert dr_tools._safe_path("workspace/banner.py") == normal
    assert dr_tools._safe_path("workspace/workspace/banner.py") == normal
    assert dr_tools._safe_path("output/workspace/foo.txt") == os.path.join(dr_tools.WORKSPACE, "output", "workspace", "foo.txt")


def test_safe_path_still_rejects_escape_after_stripping_prefix():
    with pytest.raises(ValueError):
        dr_tools._safe_path("workspace/../../etc/passwd")
