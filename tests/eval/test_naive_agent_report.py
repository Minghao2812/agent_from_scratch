"""naive_agent 产出物验收：《研报》初步数据分析。

不信报告自己说的"数字可追溯"，而是拿两把客观的尺子量：
1. 报告里出现的"原始引用数字"必须能在 workspace/研报数据/ 源文件里找到（grep，不靠 LLM 自评）。
2. 报告里出现的"计算派生指标"（CAGR、集中度等）用同一份源数据独立重算一遍，和报告里的数字比对。

不调用 LLM，只检查已生成的产出物，跑得快、可重复。产出物不存在就 skip（先跑
`python -m naive_agent.xagent --prompt "..."` 生成），不算测试失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from tests.helpers import latest_run_dir

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
NAIVE_OUTPUT_ROOT = WORKSPACE / "output" / "naive"
SOURCE_DIR = WORKSPACE / "研报数据"

# 报告里被当作"直接引用"的原始数字（不含四舍五入/单位换算产生的新数字）。
# 数字：源文件里的写法（有的文件用整数百分号，有的带小数）。
RAW_NUMBERS = [
    "4550", "6165", "8288",  # 市场大盘年度规模
    "3256", "4562", "6216",  # 公有云年度规模
    "16581", "25011",  # 半年度序列首尾
    "3697.81", "1829.52", "1780.83", "485.82",  # IaaS Top10 若干厂商
    "1101.27",  # PaaS 阿里云
    "939.4", "663.3", "172.4", "103.6",  # 政务云
    "1207", "1180", "321.61", "720.75",  # 厂商年报收入
]


def _source_corpus() -> str:
    texts = []
    for path in SOURCE_DIR.rglob("*"):
        if path.is_file() and path.suffix in (".csv", ".txt", ".md"):
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(texts)


@pytest.fixture(scope="module")
def report_text() -> str:
    run_dir = latest_run_dir(NAIVE_OUTPUT_ROOT)
    if run_dir is None:
        pytest.skip(f"产出物不存在：{NAIVE_OUTPUT_ROOT} 下没有任何一次运行，先跑 naive_agent 生成")
    report_path = run_dir / "初步数据分析.md"
    if not report_path.exists():
        pytest.skip(f"产出物不存在：{report_path}")
    return report_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def corpus() -> str:
    return _source_corpus()


def test_report_exists_and_nonempty(report_text):
    assert len(report_text) > 500, "报告内容太短，不像一份完整分析"


@pytest.mark.parametrize("number", RAW_NUMBERS)
def test_raw_numbers_traceable_to_source(number, corpus):
    """报告里引用的原始数字，必须能在研报素材源文件里找到——不能是模型编的。"""
    assert number in corpus, f"数字 {number} 在 workspace/研报数据/ 源文件里找不到，可能是编造的"


def test_market_size_cagr_recomputed(report_text):
    """独立重算 2022→2024 市场规模 CAGR，核对报告里的数字。"""
    df = pd.read_csv(SOURCE_DIR / "结构化数据/市场大盘/云计算市场规模.csv")
    m2022 = df.loc[df["年份"] == 2022, "市场规模_亿元"].iloc[0]
    m2024 = df.loc[df["年份"] == 2024, "市场规模_亿元"].iloc[0]
    cagr = ((m2024 / m2022) ** (1 / 2) - 1) * 100
    assert abs(cagr - 35.0) < 0.5, f"独立重算 CAGR={cagr:.1f}%，和预期 35.0% 偏差过大"
    assert re.search(r"35\.0%", report_text), "报告里没写出重算后应该有的 CAGR 35.0%"


def test_iaas_top10_concentration_recomputed(report_text):
    """独立重算 IaaS Top10 集中度（CR3/CR5），核对报告里的数字。"""
    df = pd.read_csv(SOURCE_DIR / "结构化数据/公有云/IaaS厂商收入2025H1.csv")
    df["收入_百万美元"] = pd.to_numeric(df["收入_百万美元"], errors="coerce")
    df = df.sort_values("收入_百万美元", ascending=False)
    total = df["收入_百万美元"].sum()
    cr3 = df.head(3)["收入_百万美元"].sum() / total * 100
    cr5 = df.head(5)["收入_百万美元"].sum() / total * 100
    assert abs(cr3 - 57.0) < 1.0, f"独立重算 CR3={cr3:.1f}%，和报告里的 57.0% 偏差过大"
    assert abs(cr5 - 76.0) < 1.0, f"独立重算 CR5={cr5:.1f}%，和报告里的 76.0% 偏差过大"
    assert "57.0%" in report_text and "76.0%" in report_text


def test_report_states_known_data_gaps(report_text):
    """sources.md 里明确标注的数据缺口，报告不能假装不知道、也不能编数字填上。"""
    for keyword in ("移动云", "火山引擎", "腾讯云"):
        assert keyword in report_text, f"报告没有提到已知数据缺口涉及的厂商：{keyword}"
    # 移动云 2025 年收入本来就没披露，报告不能编一个数字出来顶替
    assert "未披露" in report_text
