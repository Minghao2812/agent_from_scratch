"""xagent —— bare-metal coding agent，不依赖任何 agent 框架。

一个 agent 的最小运行闭环就这些：一个 while loop、一个 messages 数组、
几个工具函数、一段 dispatch 逻辑、一组停止条件。所有框架都是在这之上做包装。

运行前：
    cp .env.example .env   # 填好 key；程序会自动加载

用法（--prompt 既可以是一句话，也可以是写着任务描述的文件路径）：
    python -m naive_agent.xagent --prompt "读 workspace/sales.csv，算出每个地区全年销售额，画柱状图存成 png"
    python -m naive_agent.xagent --prompt naive_agent/prompts/report.txt --model deepseek/deepseek-v4-pro
    python -m naive_agent.xagent --prompt naive_agent/prompts/report.txt --skill   # 挂载 list_skills/load_skill
"""

import argparse
import json
import os
import time

from lib.llm import chat
from naive_agent import tools
from naive_agent.tools import DISPATCH, DISPATCH_NO_SKILLS, TOOLS, TOOLS_NO_SKILLS

MAX_TURNS = 25
# naive_agent/prompts/{dashboard,report,snake}.txt 里各自写死了一个 output/naive/xxx
# 产物路径——多次运行同一个任务（调参数、换模型对比）会互相覆盖，看不到历史版本。
# 时间戳目录由框架在这里生成、替换进 task 文本，模型看到的已经是一个具体路径，不需要
# 自己算时间戳。没有这个前缀的自由任务（banner/summarize 等探索性任务）不受影响。
OUTPUT_PREFIX = "output/naive/"


def _stamp_output_path(task: str) -> str:
    run_id = time.strftime("%Y%m%d_%H%M%S")
    return task.replace(OUTPUT_PREFIX, f"{OUTPUT_PREFIX}{run_id}/")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "RULES.md"), encoding="utf-8") as _f:
    RULES = _f.read()

SYSTEM_PROMPT = "你是 xagent，一个 coding agent。以下是你必须遵守的规则：\n\n" + RULES


def run(task: str, *, model: str | None = None, use_skills: bool = False,
        max_turns: int = MAX_TURNS, no_sandbox: bool = False) -> None:
    tool_list = TOOLS if use_skills else TOOLS_NO_SKILLS
    dispatch = DISPATCH if use_skills else DISPATCH_NO_SKILLS
    tools.SANDBOX = not no_sandbox
    task = _stamp_output_path(task)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for turn in range(1, max_turns + 1):
        print(f"\n===== 第 {turn} 轮 =====")
        response = chat(messages, tools=tool_list, model=model, max_tokens=8192)
        msg = response.choices[0].message
        messages.append(msg.model_dump())

        # 停止条件一：模型不再调工具，说明它认为任务做完了
        if not msg.tool_calls:
            print("\n[xagent 最终回复]\n" + (msg.content or ""))
            return

        # 否则逐个执行工具调用，把结果塞回 messages，进入下一轮
        for call in msg.tool_calls:
            name = call.function.name
            print(f"[调用] {name}({call.function.arguments[:200]})")
            try:
                # 参数解析失败（常见于模型生成超长内联脚本时输出被截断）也是一种
                # observation，交回给模型自己决定，不能让它直接崩掉整个 loop。
                args = json.loads(call.function.arguments)
                result = dispatch[name](**args)
            except Exception as e:  # 停止条件二：工具报错（含参数解析失败）交回给模型自己决定
                result = f"工具执行出错：{type(e).__name__}: {e}"
            print(f"[结果] {str(result)[:300]}")
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": str(result)[:8000],  # 粗暴截断，防 context 爆炸；系统性做法见 deep_research_agent
            })

    # 停止条件三：轮数用尽还没结束，强制停，避免无限循环空耗 token
    print(f"\n[达到最大轮数 {max_turns}，强制停止]")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xagent（手写 while-loop 版 naive agent）")
    parser.add_argument("--prompt", help="任务描述，或写着任务描述的文件路径")
    parser.add_argument("--model", help="覆盖 .env 里的 MODEL，例如 deepseek/deepseek-v4-pro")
    parser.add_argument("--skill", action="store_true",
                        help="挂载 list_skills/load_skill；默认不挂，对比技能发现机制时加上")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="解除 workspace 沙箱限制，允许读写仓库任意路径（对照组用）")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS,
                        help=f"工具循环轮数上限（默认 {MAX_TURNS}）")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    task = args.prompt or "列出 workspace 里有哪些文件，并说明每个文件是干什么的"
    if os.path.isfile(task):
        task = open(task, encoding="utf-8").read()
    run(task, model=args.model, use_skills=args.skill, max_turns=args.max_turns,
        no_sandbox=args.no_sandbox)
