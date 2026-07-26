"""冒烟测试环境变量辅助：缺 key / 仍是占位符 → skip。

也放 eval 测试共用的"找最新一次运行产物目录"辅助函数——四个 agent 的产物路径现在都是
`output/<agent>/<run_id>/`（run_id 由各 agent 的 run() 生成，格式 YYYYMMDD_HHMMSS，
详见各 graph.py/xagent.py 顶部注释），eval 测试要看的是"最近一次运行"，不是某个写死的
文件名。这里的共享是测试基础设施层面的，不违反四个 agent 目录各自 self-contained 的
教学设计——那条设计约束针对的是学生要阅读的 agent 代码，不针对判分脚本。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv

_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")  # YYYYMMDD_HHMMSS，跟各 agent run() 里生成的格式一致

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

_PLACEHOLDER_SNIPPETS = (
    "your-",
    "your_",
    "sk-your",
    "changeme",
    "xxx",
)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def require_env(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = env(name)
        if value is None or any(p in value.lower() for p in _PLACEHOLDER_SNIPPETS):
            missing.append(name)
        else:
            values[name] = value
    if missing:
        pytest.skip(f"未配置: {', '.join(missing)}（复制 .env.example 为 .env 后填写）")
    return values


def latest_run_dir(parent: Path) -> Path | None:
    """在 parent 下找按时间戳命名的最新一次运行目录。目录名格式 YYYYMMDD_HHMMSS，
    字典序排序等价于时间顺序，不需要额外解析/维护一个 "latest" 指针。用正则过滤目录名，
    不能看见子目录就当运行目录——parent 下可能混着 notes/、screenshots/ 这类产物内部的
    子目录，名字不像时间戳的一律忽略。parent 不存在或没有任何匹配目录时返回 None，
    调用方负责 skip。"""
    if not parent.is_dir():
        return None
    candidates = sorted(p for p in parent.iterdir() if p.is_dir() and _RUN_ID_RE.match(p.name))
    return candidates[-1] if candidates else None
