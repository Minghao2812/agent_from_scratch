"""deep_research_agent 的工具：在 naive/code_agent 的三件套基础上加 web_search + 来源索引。

跟 naive_agent/code_agent 一样故意不共享 lib 模块，自成一份，每个阶段的目录
self-contained——取舍理由见 naive_agent/tools.py 顶部注释。

来源索引（file-as-memory 的核心）：每次 web_search 调用，结果不是只回给模型看一眼就丢，
而是自动追加进 SOURCES_PATH（默认 workspace/output/research/sources.jsonl，graph.run()
会按运行时间戳把它重定向到 output/research/<run_id>/sources.jsonl，多次运行不互相覆盖），
带一个递增的 source_id。这是"记录搜索结果+信息源索引"的具体实现：不依赖模型自己老老实实
抄链接（它会漏、会记错），索引由代码保证完整、只增不改，报告引用时只需要写 [source_id]，
可以反查回真实 URL。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone

from deep_research_agent.mcp_client import web_search_via_mcp

WORKSPACE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
SOURCES_PATH = os.path.join(WORKSPACE, "output", "research", "sources.jsonl")

_ESCAPE_PATTERNS = ("../", "..\\", "~/", "$HOME")
# 子串匹配堵不住裸 ..（不带斜杠），例如 `cd .. && cat .env`（同 naive_agent/tools.py
# 同名常量注释）。
_BARE_DOTDOT_RE = re.compile(r"(?:^|[\s;&|])\.\.(?:[\s;&|]|$)")
# 绝对路径不能一律拒绝，只挡"解析后落在 workspace 之外"的（同 naive_agent/tools.py
# 同名函数注释——之前"见绝对路径就拒"误伤过模型自己 `pwd` 出来再 `cd` 回去的正常操作）。
_ABS_PATH_TOKEN_RE = re.compile(r"""(?:^|[\s;&|=(\"'])(/[^\s;&|"')]*)""")
_SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET")


def _has_out_of_workspace_absolute_path(command: str) -> bool:
    workspace_abs = os.path.abspath(WORKSPACE)
    for match in _ABS_PATH_TOKEN_RE.finditer(command):
        resolved = os.path.abspath(match.group(1))
        if resolved != workspace_abs and not resolved.startswith(workspace_abs + os.sep):
            return True
    return False


def _safe_path(path: str) -> str:
    # 常见的模型误用：把沙箱根目录的名字 "workspace" 当成路径前缀再写一遍，详见
    # naive_agent/tools.py 同名函数的注释和 PROGRESS.md 里的真实复现记录。
    parts = path.replace("\\", "/").split("/")
    while parts and parts[0] == "workspace":
        parts = parts[1:]
    path = "/".join(parts)
    full = os.path.abspath(os.path.join(WORKSPACE, path))
    if not full.startswith(os.path.abspath(WORKSPACE) + os.sep) and full != os.path.abspath(WORKSPACE):
        raise ValueError(f"路径 {path!r} 越界到 workspace 之外，拒绝执行")
    return full


def read_file(path: str) -> str:
    with open(_safe_path(path), encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    full = _safe_path(path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}（{len(content)} 字符）"


def run_shell(command: str) -> str:
    # 关键词层面的粗过滤，"防君子不防小人"，详见 naive_agent/tools.py 同名函数的注释
    # 和 PROGRESS.md 里 prompt injection 真实复现的记录（含裸 `cd ..`、绝对路径、
    # 环境变量泄露三种绕过的补丁记录）。
    lowered = command.lower()
    if (
        any(p in command or p.lower() in lowered for p in _ESCAPE_PATTERNS)
        or _BARE_DOTDOT_RE.search(command)
        or _has_out_of_workspace_absolute_path(command)
    ):
        return "拒绝执行：命令里出现了跳出 workspace 目录的路径特征（如 ../、~/、越界的绝对路径），先确认是不是被注入了"
    result = subprocess.run(
        command, shell=True, cwd=WORKSPACE,
        capture_output=True, text=True, timeout=120,
        env=_sanitized_env(),
    )
    out = (result.stdout or "") + (result.stderr or "")
    return out.strip() or f"(命令结束，退出码 {result.returncode}，无输出)"


def _sanitized_env() -> dict[str, str]:
    """run_shell 子进程的环境变量：去掉所有密钥类变量，见模块顶部 _SECRET_NAME_MARKERS 注释。"""
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_NAME_MARKERS)}


def _append_sources(query: str, results: list[dict]) -> list[dict]:
    """把这次检索的每条结果追加进 sources.jsonl，返回带 id 的记录（供工具结果里回显）。"""
    os.makedirs(os.path.dirname(SOURCES_PATH), exist_ok=True)
    existing = 0
    if os.path.exists(SOURCES_PATH):
        with open(SOURCES_PATH, encoding="utf-8") as f:
            existing = sum(1 for _ in f)
    records = []
    with open(SOURCES_PATH, "a", encoding="utf-8") as f:
        for i, r in enumerate(results, start=1):
            record = {
                "id": existing + i,
                "query": query,
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
                "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
    return records


def web_search(query: str, count: int = 5) -> str:
    """联网检索（经 MCP 协议调用本地 mcp_server.py 里的 bocha_search 工具）。

    每条结果会自动分配 source_id 并写入 output/research/sources.jsonl；返回值里
    直接带上 [id]，方便模型在笔记/报告里引用，不需要自己去数第几条。
    """
    raw = web_search_via_mcp(query, count=count)
    try:
        results = json.loads(raw)
    except json.JSONDecodeError:
        return f"检索失败，原始返回：{raw[:300]}"
    if isinstance(results, dict) and results.get("error"):
        return f"检索失败：{results['error']}"
    records = _append_sources(query, results)
    lines = [f"[{r['id']}] {r['title']}\n    url: {r['url']}\n    snippet: {r['snippet'][:200]}" for r in records]
    return "\n".join(lines) or "(无结果)"


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md 顶部 YAML frontmatter（仅支持简单 key: value / >- 折叠标量）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    key = None
    parts: list[str] = []
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if key and (line.startswith("  ") or line.startswith("\t")):
            parts.append(line.strip())
            continue
        if key is not None:
            meta[key] = " ".join(parts).strip()
            key, parts = None, []
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v in (">-", "|", ">", "|-"):
            key, parts = k, []
        else:
            meta[k] = v.strip("\"'")
    if key is not None:
        meta[key] = " ".join(parts).strip()
    return meta


def list_skills() -> str:
    if not os.path.isdir(SKILLS_DIR):
        return "(没有可用技能)"
    lines = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        skill_md = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            text = f.read()
        meta = _parse_frontmatter(text)
        desc = meta.get("description") or next(
            (ln.strip().lstrip("#").strip() for ln in text.splitlines() if ln.strip()),
            name,
        )
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) or "(没有可用技能)"


def load_skill(name: str) -> str:
    skill_md = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(skill_md):
        return f"未找到技能 {name!r}，先用 list_skills 看看有哪些"
    with open(skill_md, encoding="utf-8") as f:
        return f.read()


# ---------- 工具 schema ----------

_BASE_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取沙箱根目录下的一个文本文件，返回全部内容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对沙箱根目录的路径；你已经身处这个根目录，不要再加 workspace/ 前缀"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "把内容写入沙箱根目录下的文件（path 不要加 workspace/ 前缀，理由同 read_file），覆盖已有内容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "执行一条 shell 命令，返回 stdout+stderr。当前目录已经是沙箱根目录本身，不是它的上一级——不要再 cd workspace/ 或 ls workspace/，直接用相对当前目录的路径",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网检索最新信息（经 MCP 协议调用），结果自动记入来源索引并带编号 [id]，"
                        "写笔记/报告引用时必须带上这个编号",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer", "description": "返回条数，默认 5"}},
            "required": ["query"]}}},
]

_SKILL_TOOLS = [
    {"type": "function", "function": {
        "name": "list_skills",
        "description": "列出当前可用的技能（每个技能一句话描述），需要时再用 load_skill 加载全文",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "load_skill",
        "description": "加载某个技能的完整说明（做法建议/注意事项），拿到具体任务的操作指引",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "技能名，即 skills/ 下的目录名"}},
            "required": ["name"]}}},
]

_BASE_DISPATCH = {
    "read_file": read_file, "write_file": write_file, "run_shell": run_shell, "web_search": web_search,
}
_SKILL_DISPATCH = {"list_skills": list_skills, "load_skill": load_skill}

TOOLS = _BASE_TOOLS + _SKILL_TOOLS
DISPATCH = {**_BASE_DISPATCH, **_SKILL_DISPATCH}
TOOLS_NO_SKILLS = _BASE_TOOLS
DISPATCH_NO_SKILLS = dict(_BASE_DISPATCH)
