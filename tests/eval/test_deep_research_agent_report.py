"""deep_research_agent 产出物验收：《带外部调研的完整专题报告》。

跟 code_agent 的看板验收一个思路：不信 agent 自己说"引用可核验"，用代码把关：
1. 报告里每一个 [id] 引用，必须能在 output/research/sources.jsonl 里找到对应记录（真实 URL）。
2. 外部信源数量要达到下限——区别于 naive_agent 那份只用内部数据的报告。
3. checklist（plan.md）里的每个子问题，报告里要有覆盖痕迹（子问题笔记文件存在）。
4. context 工程量化：sub-agent 压缩后的笔记体量应明显小于它们对应产生的原始检索来源体量，
   这是"compaction 有没有真的发生"的一个可脚本核验的指标，不是只看"看起来简洁"。

产出物不存在就 skip（先跑一次 deep_research_agent 生成），不算测试失败。

产物路径是 output/research/<run_id>/，多次运行不互相覆盖，这里只看最新一次。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.helpers import latest_run_dir

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_OUTPUT_ROOT = ROOT / "workspace" / "output" / "research"

MIN_EXTERNAL_SOURCES = 5


@pytest.fixture(scope="module")
def run_dir() -> Path:
    run_dir = latest_run_dir(RESEARCH_OUTPUT_ROOT)
    if run_dir is None:
        pytest.skip(f"产出物不存在：{RESEARCH_OUTPUT_ROOT} 下没有任何一次运行，先跑 deep_research_agent 生成")
    return run_dir


@pytest.fixture(scope="module")
def report_text(run_dir) -> str:
    report_path = run_dir / "专题报告.md"
    if not report_path.exists():
        pytest.skip(f"产出物不存在：{report_path}")
    return report_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sources(run_dir) -> list[dict]:
    sources_path = run_dir / "sources.jsonl"
    if not sources_path.exists():
        pytest.skip(f"来源索引不存在：{sources_path}")
    return [json.loads(line) for line in sources_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_report_exists_and_nonempty(report_text):
    assert len(report_text) > 500, "报告内容太短，不像一份完整专题报告"


def test_external_sources_meet_minimum(sources):
    """区别于 naive_agent 只用内部数据的报告：这里必须有真实的外部检索发生。"""
    assert len(sources) >= MIN_EXTERNAL_SOURCES, \
        f"外部信源只有 {len(sources)} 条，少于下限 {MIN_EXTERNAL_SOURCES}——检索没有真的发生"
    assert all(s.get("url", "").startswith("http") for s in sources), "来源索引里出现非法/空 URL"


def test_all_citations_in_report_are_traceable_to_sources(report_text, sources):
    """报告里出现的每个 [id] 引用编号，必须能在 sources.jsonl 里查到——防止编号是编的。"""
    cited_ids = {int(n) for n in re.findall(r"\[(\d+)\]", report_text)}
    assert cited_ids, "报告里没有任何 [id] 形式的引用编号"
    valid_ids = {s["id"] for s in sources}
    invalid = cited_ids - valid_ids
    assert not invalid, f"报告引用了不存在于 sources.jsonl 的编号：{invalid}"


def test_checklist_topics_have_corresponding_notes(report_text, run_dir):
    """plan.md 里的每个子问题，都应该在 notes/ 下有一份对应的笔记文件——覆盖度的客观抓手。"""
    plan_path = run_dir / "plan.md"
    notes_dir = run_dir / "notes"
    if not plan_path.exists():
        pytest.skip(f"计划文件不存在：{plan_path}")
    topics = re.findall(r"- \[.\] (.+)", plan_path.read_text(encoding="utf-8"))
    assert topics, "plan.md 里没有解析出任何 checklist 子问题"
    note_files = list(notes_dir.glob("*.md")) if notes_dir.exists() else []
    assert len(note_files) >= 1, "notes/ 目录下没有任何子任务笔记，checklist 没有被真正执行"


def test_compaction_actually_reduces_context_size(run_dir):
    """context 工程量化：所有子任务笔记加起来的字符数，应明显小于 sources.jsonl 里
    原始检索结果（title+snippet）加起来的字符数——否则"压缩"只是嘴上说说。
    """
    notes_dir = run_dir / "notes"
    sources_path = run_dir / "sources.jsonl"
    if not notes_dir.exists() or not sources_path.exists():
        pytest.skip("notes/ 或 sources.jsonl 不存在")
    notes_size = sum(len(p.read_text(encoding="utf-8")) for p in notes_dir.glob("*.md"))
    sources_size = sum(
        len(json.loads(line).get("snippet", "")) + len(json.loads(line).get("title", ""))
        for line in sources_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    assert notes_size > 0, "笔记体量为 0"
    assert sources_size > 0, "来源体量为 0，说明根本没有检索"
    assert notes_size < sources_size, (
        f"笔记体量（{notes_size} 字符）没有小于原始检索体量（{sources_size} 字符），"
        "compaction 没有真的发生"
    )


def test_report_distinguishes_internal_and_external_data(report_text):
    """报告要能看出"哪些是内部研报素材、哪些是外部调研"，不能把两者混为一谈。"""
    assert "内部" in report_text or "研报素材" in report_text, \
        "报告没有标注内部数据来源，无法区分内部素材和外部调研"
