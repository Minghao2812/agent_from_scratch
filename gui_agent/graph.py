"""gui agent —— 用 CDP 虚拟时间 + VLM 截图决策操作 naive_agent 写出来的贪吃蛇。

跟 naive/code/deep_research 三个 agent 的关键区别：
- 需要视觉模型（DeepSeek 文本模型看不了图），默认走 Qwen-VL（复用 .env 已有的
  QWEN_API_KEY/QWEN_BASE_URL/QWEN_VL_MODEL，见下方 DEFAULT_* 的取值逻辑）。
- 每一步都是独立的一次 (system + 当前这对截图) 调用，不像另外三个 agent 那样把完整
  对话历史一路带下去——截图转 base64 后单张就有几十 KB，把历史全部累积会让 context
  很快爆炸，而"上一步做了什么"这个信息，两张截图本身（尤其带着上一帧）已经足够传达。
- 决策不用 function calling：每步只需要从一个很窄的动作空间里选一个，用 tools схема
  没有额外收益，反而多一层多模态输入下 tool-calling 支持是否稳定的不确定性；跟
  webdev-arena/cdp_verifier 的做法一致，直接让模型输出一段 JSON 文本，解析失败当
  wait 处理（不让一次解析失败打断整个循环）。
- 自适应（对照 cdp_verifier 的 summarize_code）：启动时先读 HTML 源码做一次摘要，
  抽出颜色/控制方式/是否自动开局/tick_ms，注入 system prompt；决策时要求严格依据
  这份背景信息识别截图元素。skill 只留通用玩法提示，不写死颜色——naive_agent 重生
  一版 snake.html 也能跟着适配。tick 用源码正则优先、摘要字段其次，budget=tick*2。

流程（对照 webdev-arena/cdp_verifier 的 _loop，取舍写在 browser.py 顶部注释）：
    setup：读源码 summarize -> 起浏览器 -> 打开页面 -> 按需点开始 -> pause -> 截图 s0
    decide 节点：(s_prev, s_cur) 两张截图 + system prompt（含背景信息）-> VLM -> 动作
    act 节点：按需按键 -> resume(budget) -> sleep(settle) -> pause -> 截图 s_next
    route：动作是 stop，或到达 max_steps -> END；否则回 decide

用法：
    python -m gui_agent.graph                    # 默认有头运行，自动找最新 snake.html
    python -m gui_agent.graph --max-steps 80 --headless   # 跑 eval/CI 时不弹窗口
    python -m gui_agent.graph --model openai/glm-5.2      # 换模型；换 key/base_url 直接改 .env
    python -m gui_agent.graph --skill ""                 # 关掉可选玩法提示，只靠源码摘要
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from playwright.async_api import async_playwright

from gui_agent.browser import ActionExecutor, CDPController, normalize_key
from lib.llm import chat

ROOT = Path(__file__).resolve().parents[1]
NAIVE_OUTPUT_ROOT = ROOT / "workspace" / "output" / "naive"
OUTPUT_DIR = ROOT / "workspace" / "output" / "gui"
_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")  # 跟 naive_agent.xagent 生成的 run_id 格式一致


def default_snake_html() -> Path:
    """找 naive_agent 最近一次产出的 snake.html。

    naive_agent 会把产物写到 output/naive/<run_id>/snake.html（多次运行不互相覆盖），
    这里按目录名字典序取最新一次；找不到时返回旧的平铺路径，留给调用方报更清楚的错。
    """
    if NAIVE_OUTPUT_ROOT.is_dir():
        candidates = sorted(
            p for p in NAIVE_OUTPUT_ROOT.iterdir()
            if p.is_dir() and _RUN_ID_RE.match(p.name) and (p / "snake.html").is_file()
        )
        if candidates:
            return candidates[-1] / "snake.html"
    return NAIVE_OUTPUT_ROOT / "snake.html"

DEFAULT_TICK_MS = 150  # 摘要/正则都拿不到时的兜底；经验上常见游戏 setInterval 在 100~200
TICKS_PER_STEP = 2  # budget = tick_ms * 2，严格大于一帧，避免虚拟时间卡在 tick 边界导致两帧相同
SETTLE_S = 0.2
INIT_WAIT_S = 0.5
MAX_STEPS_DEFAULT = 60

# Qwen-VL：复用 .env 里给冒烟测试用的 QWEN_* 凭证，不需要学员再单独配一套。
# GUI_MODEL/GUI_API_KEY/GUI_API_BASE 三个环境变量留作换其它视觉模型（GLM-4V等）时的覆盖入口，
# 跟 lib/llm.py 里 MODEL 的可换性哲学一致。
DEFAULT_MODEL = os.environ.get("GUI_MODEL") or ("openai/" + os.environ.get("QWEN_VL_MODEL", "qwen3-vl-plus"))
DEFAULT_API_KEY = os.environ.get("GUI_API_KEY") or os.environ.get("QWEN_API_KEY")
DEFAULT_API_BASE = os.environ.get("GUI_API_BASE") or os.environ.get("QWEN_BASE_URL")

SKILLS_DIR = Path(__file__).parent / "skills"

with open(Path(__file__).parent / "RULES.md", encoding="utf-8") as _f:
    RULES = _f.read()

_ACTION_SCHEMA = (
    '严格输出如下 JSON，不要有任何其它文字：\n'
    '{"thinking": "一句话说明看到了什么、打算怎么做", "action": "ArrowUp|ArrowDown|ArrowLeft|ArrowRight|wait|stop"}'
)

# 对照 webdev-arena/cdp_verifier 的 summarize_code：颜色/控制/tick 不写死在 skill 里，
# 每次启动从当前 HTML 源码摘要出来——naive_agent 重生一版 snake.html 也能跟着适配。
_SUMMARIZE_SYSTEM = "你是前端代码分析助手，只输出 JSON，不要其它文字。"
_SUMMARIZE_USER = """分析下面 HTML/JS 源码，提取自动化键盘操控所需的关键信息。只输出 JSON：
{{
  "app_name": "应用名",
  "control_method": "操作方式（如方向键）",
  "elements": {{"snake_head": "颜色/形状", "snake_body": "颜色/形状", "food": "颜色/形状", "other": "其它关键元素"}},
  "main_logic": "核心逻辑一两句",
  "auto_start": true或false,
  "tick_ms": 游戏主循环间隔毫秒数（从 TICK_MS/setTimeout/setInterval 读；读不到就写 150）,
  "failure_criteria": "失败条件",
  "game_over_signal": "结束画面长什么样"
}}

源码：
```
{source}
```
"""


def _load_skill(name: str) -> str:
    """读 skills/<name>/SKILL.md 的正文（去掉 frontmatter，那部分是给人看的元信息）。"""
    skill_md = SKILLS_DIR / name / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"未找到技能 {name!r}，SKILLS_DIR={SKILLS_DIR}")
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4 :]
    return text.strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _tick_ms_from_source(source: str) -> int | None:
    """从源码正则抠 tick，不依赖 LLM——比摘要里的 tick_ms 字段更稳。"""
    for pattern in (
        r"\bTICK_MS\s*=\s*(\d+)",
        r"setTimeout\s*\([^,]+,\s*(\d+)\s*\)",
        r"setInterval\s*\([^,]+,\s*(\d+)\s*\)",
    ):
        m = re.search(pattern, source)
        if m:
            val = int(m.group(1))
            if 20 <= val <= 2000:
                return val
    return None


def summarize_html(source: str, *, model: str | None = None) -> dict[str, Any]:
    """读 HTML 源码，抽出颜色/控制方式/tick 等背景信息（对照 cdp_verifier.summarize_code）。"""
    response = chat(
        [
            {"role": "system", "content": _SUMMARIZE_SYSTEM},
            {"role": "user", "content": _SUMMARIZE_USER.format(source=source[:12000])},
        ],
        model=model or DEFAULT_MODEL,
        max_tokens=600,
        api_key=DEFAULT_API_KEY,
        api_base=DEFAULT_API_BASE,
    )
    raw = response.choices[0].message.content or ""
    data = _parse_json_object(raw)
    # tick：源码正则优先，摘要字段其次，最后才用默认值
    tick = _tick_ms_from_source(source)
    if tick is None:
        try:
            tick = int(data.get("tick_ms") or DEFAULT_TICK_MS)
        except (TypeError, ValueError):
            tick = DEFAULT_TICK_MS
    data["tick_ms"] = tick if 20 <= tick <= 2000 else DEFAULT_TICK_MS
    return data


def _build_system_prompt(skill: str | None, code_summary: dict[str, Any] | None = None) -> str:
    parts = ["你是 gui agent，通过截图观察网页画面并决定下一步动作。以下是你必须遵守的规则：\n\n" + RULES]
    if code_summary:
        parts.append(
            "以下是当前页面的背景信息（从源码摘要，请严格据此识别截图中的元素）：\n\n"
            + json.dumps(code_summary, ensure_ascii=False, indent=2)
        )
    if skill:
        parts.append(f"以下是可选的玩法提示（技能：{skill}；具体外观以背景信息为准）：\n\n" + _load_skill(skill))
    parts.append(_ACTION_SCHEMA)
    return "\n\n".join(parts)

# _env 只放浏览器/CDP 这些活的运行期对象——不是数据，是资源handle，序列化没有意义，
# 放进 GuiState 也不会被 langgraph 当成真正的图数据来用。task/model/system_prompt 等
# 纯数据字段没有这个限制，就直接放 GuiState 里，跟着图正常流转（不用 _env 转一手）。
_env: dict[str, Any] = {}


class GuiState(TypedDict):
    screenshot_prev: str | None
    screenshot_cur: str
    step: int
    action: str
    task: str
    model: str
    system_prompt: str
    max_steps: int


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("utf-8")


def _parse_action(raw: str) -> str:
    """从 VLM 原始输出里解析出动作；解析失败按 wait 处理，不打断循环。"""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return "wait"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "wait"
    action = str(data.get("action", "")).strip()
    upper = action.upper()
    if upper == "STOP":
        return "stop"
    if upper in ("WAIT", "CONTINUE", ""):
        return "wait"
    return action


async def adecide_node(state: GuiState) -> dict[str, Any]:
    s_prev = state["screenshot_prev"] or state["screenshot_cur"]
    s_cur = state["screenshot_cur"]
    messages = [
        {"role": "system", "content": state["system_prompt"]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": state["task"]},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(s_prev)}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(s_cur)}"}},
            ],
        },
    ]
    response = await asyncio.to_thread(
        chat,
        messages,
        model=state["model"],
        max_tokens=300,
        api_key=DEFAULT_API_KEY,
        api_base=DEFAULT_API_BASE,
    )
    raw = response.choices[0].message.content or ""
    action = _parse_action(raw)
    print(f"[gui agent] step={state['step']} raw={raw.strip()[:160]!r} -> action={action}")
    return {"action": action}


async def aact_node(state: GuiState) -> dict[str, Any]:
    page = _env["page"]
    cdp: CDPController = _env["cdp"]
    executor: ActionExecutor = _env["executor"]
    action = state["action"]
    step = state["step"] + 1

    if action == "stop":
        return {"step": step}

    key = None if action == "wait" else normalize_key(action)
    if key:
        await executor.press(page, key)

    await cdp.resume(_env["budget_ms"])
    await asyncio.sleep(SETTLE_S)
    await cdp.pause()
    shot_path = _env["shot_dir"] / f"s{step}.png"
    shot = await cdp.screenshot(str(shot_path))

    return {
        "screenshot_prev": state["screenshot_cur"],
        "screenshot_cur": shot or state["screenshot_cur"],
        "step": step,
    }


def route_after_act(state: GuiState) -> str:
    # 游戏是否结束靠 agent 自己从截图判断并输出 stop（见 RULES.md 第 3 条）；harness
    # 不读游戏内部 JS 变量当停止条件——那是 naive_agent 产出物内部的实现细节，不应该被
    # gui_agent 依赖。max_steps 兜底，避免识别失败时无限循环。
    if state["action"] == "stop" or state["step"] >= state["max_steps"]:
        return END
    return "decide"


def build_graph():
    graph = StateGraph(GuiState)
    graph.add_node("decide", adecide_node)
    graph.add_node("act", aact_node)
    graph.set_entry_point("decide")
    graph.add_edge("decide", "act")
    graph.add_conditional_edges("act", route_after_act, {"decide": "decide", END: END})
    return graph.compile()


async def _run_async(
    html_path: Path, task: str, max_steps: int, model: str | None,
    headless: bool, output_dir: Path, skill: str | None,
) -> dict[str, Any]:
    # 每次运行的产物（截图 + snake_result.json）都收进 output_dir/<run_id>/ 下，不用
    # 文件名前缀区分——之前 screenshots/ 是所有运行共用一个平铺目录，snake_result.json
    # 更是直接覆盖，多次运行看不到历史版本。run_id 由框架这里生成，agent 的决策逻辑
    # 完全不感知这层目录结构。
    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / run_id
    shot_dir = run_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    model = model or DEFAULT_MODEL
    source = html_path.read_text(encoding="utf-8")
    # 先摘要源码再开浏览器：颜色/控制/tick 都从这一次产物里来，不绑死某版 snake.html。
    # 对照 cdp_verifier：不是"纯瞎看图"，而是 code_summary + 截图；summary 失败也不崩，
    # tick 还能从源码正则兜底。
    print("[gui agent] 正在摘要页面源码…")
    code_summary = await asyncio.to_thread(summarize_html, source, model=model)
    tick_ms = int(code_summary.get("tick_ms") or DEFAULT_TICK_MS)
    budget_ms = tick_ms * TICKS_PER_STEP
    print(f"[gui agent] 摘要完成: tick_ms={tick_ms}, budget_ms={budget_ms}, summary={json.dumps(code_summary, ensure_ascii=False)[:300]}")
    (run_dir / "code_summary.json").write_text(
        json.dumps(code_summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(f"file://{html_path.resolve()}", wait_until="domcontentloaded")
            await asyncio.sleep(INIT_WAIT_S)

            cdp_session = await ctx.new_cdp_session(page)
            cdp = CDPController(cdp_session)

            # 开局兜底：有 #startBtn 就点，没有（自动开局的版本）就跳过。
            # 不走 page.click()，理由跟按键一致——不进 CDP Input pipeline。
            await page.evaluate(
                "const b = document.getElementById('startBtn'); if (b) b.click();"
            )

            await cdp.pause()
            s0 = await cdp.screenshot(str(shot_dir / "s0.png"))
            if not s0:
                raise RuntimeError("初始截图失败")

            _env.update(
                page=page, cdp=cdp, executor=ActionExecutor(),
                shot_dir=shot_dir, run_id=run_id, budget_ms=budget_ms,
            )

            app = build_graph()
            state: GuiState = {
                "screenshot_prev": None, "screenshot_cur": s0, "step": 0, "action": "",
                "task": task, "model": model, "max_steps": max_steps,
                "system_prompt": _build_system_prompt(skill, code_summary),
            }
            final_state = await app.ainvoke(state)

            # 得分元素 id 不绑死某一次产出（#score / #scoreDisplay 都见过）
            score = int(await page.evaluate(
                "() => { const el = document.getElementById('score') || document.getElementById('scoreDisplay');"
                " return el ? parseInt(el.textContent, 10) || 0 : 0; }"
            ))
        finally:
            await browser.close()

    return {
        "run_id": run_id, "steps": final_state["step"], "score": score,
        "last_action": final_state["action"], "tick_ms": tick_ms, "budget_ms": budget_ms,
    }


def run(
    html_path: str | Path | None = None,
    task: str = "操作贪吃蛇游戏：尽量吃到食物，同时避免撞墙和撞到自己的身体。",
    *,
    max_steps: int = MAX_STEPS_DEFAULT,
    model: str | None = None,
    headless: bool = False,
    output_dir: str | Path = OUTPUT_DIR,
    skill: str | None = "snake",
) -> dict[str, Any]:
    html_path = Path(html_path) if html_path else default_snake_html()
    if not html_path.is_file():
        raise FileNotFoundError(
            f"找不到 snake.html：{html_path}\n"
            f"请先跑 naive_agent 产出贪吃蛇：python -m naive_agent.xagent --prompt naive_agent/prompts/snake.txt"
        )
    output_dir = Path(output_dir)
    result = asyncio.run(
        _run_async(html_path, task, max_steps, model, headless, output_dir, skill)
    )
    run_dir = output_dir / result["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "snake_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[gui agent 结束] {result}\n结果已写入 {result_path}")
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="gui agent（CDP 虚拟时间 + VLM 操作贪吃蛇）")
    parser.add_argument("--html", default=None,
                         help="待操作的 HTML 文件路径；默认自动找 naive_agent 最新一次产出的 snake.html")
    parser.add_argument("--prompt", help="任务描述，或写着任务描述的文件路径")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS_DEFAULT)
    parser.add_argument("--model", help="覆盖默认 Qwen-VL 模型，例如 openai/glm-5.2（换 key/base_url 直接改 .env）")
    parser.add_argument("--headless", action="store_true",
                         help="不弹出浏览器窗口（跑 eval/CI 时用）；默认有头运行，方便直接看交互过程")
    parser.add_argument("--skill", default="snake",
                         help="挂载的技能名（skills/<name>/SKILL.md，跟目标页面对应）；传空字符串关闭")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    task = args.prompt or "操作贪吃蛇游戏：尽量吃到食物，同时避免撞墙和撞到自己的身体。"
    if os.path.isfile(task):
        task = open(task, encoding="utf-8").read().strip()
    run(
        args.html, task, max_steps=args.max_steps, model=args.model,
        headless=args.headless, skill=args.skill or None,
    )
