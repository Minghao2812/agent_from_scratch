"""code_agent 产出物验收：《研报》经营数据看板。

三层验收，一层比一层不信任 agent 自己的话：
1. 产出物本身存在（dashboard.html + 独立的 verify.py）。
2. 真的把 agent 自己写的 verify.py 当子进程跑一遍，必须 exit 0。
3. 不止信 agent 的 verify.py——本测试再独立重算 1-2 个关键指标，防止 verify.py
   本身也是编的（"agent 既是考生又是判官"的 reward hacking 风险）。

不调用 LLM，跑得快、可重复。产出物不存在就 skip。产物路径是 output/code/<run_id>/，
多次运行不互相覆盖，这里只看最新一次。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.helpers import latest_run_dir

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
CODE_OUTPUT_ROOT = WORKSPACE / "output" / "code"


@pytest.fixture(scope="module")
def out_dir() -> Path:
    run_dir = latest_run_dir(CODE_OUTPUT_ROOT)
    if run_dir is None:
        pytest.skip(f"产出物不存在：{CODE_OUTPUT_ROOT} 下没有任何一次运行，先跑 code_agent 生成")
    return run_dir


@pytest.fixture(scope="module")
def dashboard_html(out_dir) -> str:
    dashboard = out_dir / "dashboard.html"
    if not dashboard.exists():
        pytest.skip(f"产出物不存在：{dashboard}")
    return dashboard.read_text(encoding="utf-8")


def test_dashboard_and_verify_script_exist(out_dir):
    dashboard = out_dir / "dashboard.html"
    verify_script = out_dir / "verify.py"
    assert dashboard.exists(), "dashboard.html 不存在"
    assert verify_script.exists(), "verify.py 不存在——code agent 必须自带独立验证脚本"
    assert dashboard.stat().st_size > 10_000, "dashboard.html 太小，不像包含真实图表"


def test_dashboard_is_single_offline_html(out_dir, dashboard_html):
    """单一离线 HTML：禁止 CDN；禁止旁路 .js；禁止把 Plotly 整库塞进单文件。

    推荐形态是 matplotlib PNG 的 data:image/png;base64,...（见 dashboard skill）。
    """
    remote_srcs = re.findall(r'<script[^>]+src=["\'](https?://[^"\']+)["\']', dashboard_html)
    assert not remote_srcs, f"不能有 CDN/远程 <script src>：{remote_srcs}"

    local_srcs = re.findall(r'<script[^>]+src=["\'](?!https?://|data:)([^"\']+)["\']', dashboard_html)
    assert not local_srcs, (
        f"要求单一 HTML，不能再旁路引用本地脚本：{local_srcs}；"
        "请把图以 base64 嵌入，或把小库真正 inline 进同一文件"
    )
    assert not (out_dir / "plotly.min.js").exists(), (
        "不应再生成旁路 plotly.min.js；请输出单一 dashboard.html"
    )

    size = (out_dir / "dashboard.html").stat().st_size
    assert size < 2_000_000, (
        f"dashboard.html 过大（{size} bytes），很可能又把 plotly.js 整库 inline 进去了"
    )

    chart_markers = (
        "data:image/png;base64,",
        "data:image/svg+xml",
        "Plotly.newPlot",
        "new Chart(",
        "echarts.init(",
        'getContext("2d")',
        "<canvas",
        "<svg",
        "bar-fill",   # 纯 CSS 条形图
        "vbar-fill",
    )
    assert any(m in dashboard_html for m in chart_markers), \
        "没有识别出嵌入图片/图表绘制，产物可能不是真看板"

    # Cursor/IDE 打开含数十万字符单行（典型是整行 base64）时会一直转圈；锁住行宽上限
    max_line = max((len(l) for l in dashboard_html.splitlines()), default=0)
    assert max_line < 5000, (
        f"dashboard.html 存在超长行（{max_line} chars），IDE 预览会假死；"
        "不要把整段 base64 写在一行，优先纯 HTML/CSS 图"
    )


def test_agent_own_verify_script_passes(out_dir):
    """真的把 agent 写的 verify.py 当子进程跑一遍，不是只看它嘴上说"通过"。

    只认 exit code：agent 每次写的 verify.py 输出文案不固定，exit code 才是稳定契约。
    """
    verify_script = out_dir / "verify.py"
    if not verify_script.exists():
        pytest.skip("verify.py 不存在")
    result = subprocess.run(
        [sys.executable, str(verify_script.relative_to(WORKSPACE))],
        cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"verify.py 自检失败：\n{result.stdout}\n{result.stderr}"


def test_key_numbers_independently_recomputed(dashboard_html):
    """防 reward hacking：从源 CSV 独立重算关键指标，确认明文出现在 dashboard 里。"""
    market_df = pd.read_csv(WORKSPACE / "研报数据/结构化数据/市场大盘/云计算市场规模.csv")
    latest = market_df.iloc[-1]
    yoy = f"{float(latest['同比增速_pct']):.1f}"
    assert yoy in dashboard_html, f"2024年市场同比增速 {yoy}（独立重算）没有出现在 dashboard 里"

    size = f"{float(latest['市场规模_亿元']):.0f}"
    assert size in dashboard_html or f"{float(latest['市场规模_亿元']):.1f}" in dashboard_html, \
        f"2024年市场规模 {size}（独立重算）没有出现在 dashboard 里"

    iaas_df = pd.read_csv(WORKSPACE / "研报数据/结构化数据/公有云/IaaS厂商收入2025H1.csv")
    top1 = iaas_df.sort_values("收入_百万美元", ascending=False).iloc[0]
    assert str(top1["厂商"]) in dashboard_html, "IaaS Top1 厂商名没有出现在 dashboard 里"
