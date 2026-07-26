"""xagent 的工具：read_file / write_file / run_shell 三件套 + list_skills / load_skill。

naive_agent 和 code_agent 的工具实现基本一样，但故意各自维护一份、不共享成 lib 模块——
这是设计取舍：naive_agent 是"先讲清楚一个 agent 内部长什么样"，code_agent 是"框架版怎么
改造它"，两边各自 self-contained，读一个文件夹就能看懂一个阶段，不用跳来跳去追代码
是从哪个共享库来的。多一次复制，少一次跨文件夹跳转。

list_skills / load_skill 对应 Anthropic Agent Skills 的机制——模型先看到一句话描述，
需要时自己决定加载哪个技能的完整说明，而不是把所有任务的详细做法都塞进 system prompt
（那是 rule 的做法，常驻、成本高）。
"""

from __future__ import annotations

import os
import re
import subprocess

SANDBOX = True  # False 时解除 workspace 沙箱限制，允许访问仓库任意路径（对照组用）

WORKSPACE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

_ESCAPE_PATTERNS = ("../", "..\\", "~/", "$HOME")
# 子串匹配堵不住一类真实复现过的绕过：裸 ..（不带斜杠），例如 `cd .. && cat .env`——
# `cd` 不需要斜杠就能跳出 workspace。用正则补上。
_BARE_DOTDOT_RE = re.compile(r"(?:^|[\s;&|])\.\.(?:[\s;&|]|$)")
# 绝对路径不能一律拒绝——模型经常会先 `pwd` 确认当前目录，再原样 `cd` 回这个绝对路径
# （这本身完全无害，之前"见绝对路径就拒"误伤过这种正常操作，逼模型多绕好几轮才找到
# 能跑通的写法，见 PROGRESS.md）。真正要挡的是"解析后落在 workspace 之外"的绝对路径，
# 所以这里提取命令里所有绝对路径 token，逐个 resolve 后跟 WORKSPACE 做 containment
# 检查，跟 _safe_path 的判断逻辑一致，只是应用对象是 shell 命令字符串而不是单个参数。
_ABS_PATH_TOKEN_RE = re.compile(r"""(?:^|[\s;&|=(\"'])(/[^\s;&|"')]*)""")


def _has_out_of_workspace_absolute_path(command: str) -> bool:
    workspace_abs = os.path.abspath(WORKSPACE)
    for match in _ABS_PATH_TOKEN_RE.finditer(command):
        resolved = os.path.abspath(match.group(1))
        if resolved != workspace_abs and not resolved.startswith(workspace_abs + os.sep):
            return True
    return False
# 传给 run_shell 子进程的环境变量：过滤掉所有密钥类变量。run_shell 默认继承整个父
# 进程的 os.environ（lib/llm.py 已用 load_dotenv 把 .env 里全部 provider 的 key 灌
# 进去），之前完全没做隔离——不需要碰任何文件路径，一句 `echo $DEEPSEEK_API_KEY`
# 或 `env` 就能把所有 key 读出来，路径过滤对这条泄露路径完全无效（真实复现过）。
# 按变量名包含 KEY/TOKEN/SECRET 过滤，覆盖 .env.example 里现有及未来新增的 provider
# （统一遵循 *_API_KEY 命名约定），不用逐个维护 provider 名单。
_SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET")


def _safe_path(path: str) -> str:
    if not SANDBOX:
        # 对照组：解除沙箱，直接解析路径（允许访问仓库任意位置）
        return os.path.abspath(os.path.join(WORKSPACE, path))
    # 常见的模型误用：把沙箱根目录的名字 "workspace" 当成路径前缀再写一遍，例如
    # 传 "workspace/banner.py"（真实复现过：模型在 workspace 内部执行 `ls workspace/`
    # 扑空后，误以为 workspace 目录本身不存在，此后所有路径都手动带上这个前缀，
    # 见 PROGRESS.md）。工具描述"相对 workspace 的路径"这句话本身有歧义是根因——
    # 剥离开头所有多余的 workspace 前缀（哪怕重复出现），而不是照单全收造出
    # workspace/workspace/ 嵌套目录。
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
    # 危险操作在这里发生。read_file/write_file 有 _safe_path 挡越界路径，但 shell
    # 命令字符串本身不经过这层检查——之前这里完全没做限制，真实跑 prompt injection
    # demo 时就是从这个洞把 .env 的 key exfiltrate 出去的（见 PROGRESS.md）。
    # 下面是关键词层面的粗过滤，属于"防君子不防小人"：能挡住已复现过的几种攻击，但
    # 不是可靠的沙箱——黑名单本身就堵不完（裸 `cd ..`、任意绝对路径都是事后才补上
    # 的洞），生产系统要换成 OS 级隔离（容器/chroot/Landlock），而不是关键词黑名单。
    if SANDBOX:
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
    """列出可用技能（只给 description，不给全文——全文要用 load_skill 按需加载）。"""
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


TOOLS = [
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
        "description": "执行一条 shell 命令，返回 stdout+stderr。当前目录已经是沙箱根目录本身，不是它的上一级——不要再 cd workspace/ 或 ls workspace/，直接用相对当前目录的路径。可用来跑 Python 脚本、装包、看文件列表",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}},
            "required": ["command"]}}},
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

DISPATCH = {
    "read_file": read_file, "write_file": write_file, "run_shell": run_shell,
    "list_skills": list_skills, "load_skill": load_skill,
}

# 默认工具子集（无 skills）；传 --skill 时改用完整 TOOLS/DISPATCH
TOOLS_NO_SKILLS = [t for t in TOOLS if t["function"]["name"] not in ("list_skills", "load_skill")]
DISPATCH_NO_SKILLS = {k: v for k, v in DISPATCH.items() if k not in ("list_skills", "load_skill")}
