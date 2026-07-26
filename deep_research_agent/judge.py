"""rubric + LLM-judge：主观评估专题报告质量，用来对比不同 model/prompt 的效果。

跟 tests/eval 的关系：tests/eval 判定"有没有造假"（引用可核验/外部信源数量/compaction），
是硬性验收标准，脚本说了算；这里判定"写得好不好"，是软指标，LLM-judge 说了算，仅供参考——
两者不能互相替代，见 rubric.md 开头的说明。

用法（rubric.md 里有完整的"跑两次、换模型对比"流程）：
    python -m deep_research_agent.judge workspace/output/research/专题报告.md
    python -m deep_research_agent.judge report_a.md report_b.md --model deepseek/deepseek-v4-pro
"""

from __future__ import annotations

import argparse
import json
import re

from lib.llm import chat

DIMENSIONS = [
    ("comprehensiveness", "覆盖度", 0.25),
    ("insight", "洞察深度", 0.25),
    ("instruction_following", "指令遵循", 0.20),
    ("readability", "可读性", 0.15),
    ("citation_trust", "引用可信度", 0.15),
]

JUDGE_SYSTEM = (
    "你是一个严格的研究报告评审员，参考 DeepResearch Bench 的 RACE 框架打分。"
    "对给定报告，从 5 个维度各打 1-5 分并给一句话理由，只输出 JSON，格式："
    '{"comprehensiveness": {"score": 4, "reason": "..."}, "insight": {...}, '
    '"instruction_following": {...}, "readability": {...}, "citation_trust": {...}}'
)


def judge_report(report_text: str, *, model: str | None = None, attempts: int = 2) -> dict:
    """打分偶尔会拿到空/非 JSON 输出（真实碰到过一次，见 PROGRESS.md），重试一次再放弃。"""
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": f"待评审报告：\n\n{report_text[:12000]}"},
    ]
    for _ in range(attempts):
        response = chat(messages, model=model, max_tokens=1024)
        text = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
    return {}


def weighted_total(scores: dict) -> float:
    total = 0.0
    for key, _, weight in DIMENSIONS:
        total += scores.get(key, {}).get("score", 0) * weight
    return round(total, 2)


def print_comparison(results: dict[str, dict]) -> None:
    header = f"{'维度':<14}" + "".join(f"{name:<20}" for name in results)
    print(header)
    for key, label, _ in DIMENSIONS:
        row = f"{label:<14}"
        for scores in results.values():
            s = scores.get(key, {}).get("score", "-")
            row += f"{s!s:<20}"
        print(row)
    print(f"{'加权总分':<14}" + "".join(f"{weighted_total(s):<20}" for s in results.values()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="专题报告 rubric + LLM-judge 主观评估")
    parser.add_argument("reports", nargs="+", help="一份或多份报告 .md 文件路径")
    parser.add_argument("--model", help="评审用的模型，默认同 .env 里的 MODEL")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    results = {}
    for path in args.reports:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        scores = judge_report(text, model=args.model)
        results[path] = scores
        print(f"\n=== {path} ===")
        for key, label, _ in DIMENSIONS:
            d = scores.get(key, {})
            print(f"  {label}: {d.get('score', '-')} —— {d.get('reason', '')}")
        print(f"  加权总分: {weighted_total(scores)}")
    if len(results) > 1:
        print("\n=== 对比 ===")
        print_comparison(results)
