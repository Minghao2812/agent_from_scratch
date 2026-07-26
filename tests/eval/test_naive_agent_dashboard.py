"""naive_agent 数据大屏产出物的最基本验收：存在、够大、离线自包含。

不做逐指标重算（naive_agent 阶段的重点是"跑通"，数字可追溯的严格校验放在
test_naive_agent_report.py 里针对研报分析已经覆盖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import latest_run_dir

ROOT = Path(__file__).resolve().parents[2]
NAIVE_OUTPUT_ROOT = ROOT / "workspace" / "output" / "naive"


@pytest.fixture(scope="module")
def dashboard_path() -> Path:
    run_dir = latest_run_dir(NAIVE_OUTPUT_ROOT)
    if run_dir is None:
        pytest.skip(f"产出物不存在：{NAIVE_OUTPUT_ROOT} 下没有任何一次运行")
    dashboard = run_dir / "dashboard.html"
    if not dashboard.exists():
        pytest.skip(f"产出物不存在：{dashboard}")
    return dashboard


@pytest.fixture(scope="module")
def dashboard_html(dashboard_path) -> str:
    return dashboard_path.read_text(encoding="utf-8")


def test_dashboard_exists_and_nonempty(dashboard_path):
    assert dashboard_path.exists()
    assert dashboard_path.stat().st_size > 10_000


def test_dashboard_is_offline_self_contained(dashboard_path, dashboard_html):
    """回归测试：禁止 CDN 外链；允许同目录相对路径（如 plotly.min.js）。

    prompt 没有指定具体用哪个图表库，这里不绑定某一个库的 API 名字，只认
    「确实调了某个真图表库、且没有 http(s) 外链脚本」这个不变量。
    """
    import re

    remote_srcs = re.findall(r'<script[^>]+src=["\'](https?://[^"\']+)["\']', dashboard_html)
    assert not remote_srcs, f"不能有 CDN/远程 <script src>：{remote_srcs}"

    local_srcs = re.findall(r'<script[^>]+src=["\'](?!https?://)([^"\']+)["\']', dashboard_html)
    for src in local_srcs:
        assert (dashboard_path.parent / src).exists(), f"HTML 引用了本地脚本 {src!r}，但同目录不存在"

    chart_lib_markers = (
        "Plotly.newPlot", "new Chart(", "echarts.init(",
        'getContext("2d")', "getContext('2d')", "<canvas",
    )
    has_marker = any(m in dashboard_html for m in chart_lib_markers)
    has_local_lib = bool(local_srcs)
    assert has_marker or has_local_lib, "没有识别出任何已知图表库/canvas 绘制，产物可能不是真图表"
