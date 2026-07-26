# 专题报告主观评分 rubric

客观测试（`tests/eval/test_deep_research_agent_report.py`）只能判定"有没有造假"（引用可核验、
外部信源数量、compaction 是否发生），判定不了"写得好不好"。这份 rubric 参考 DeepResearch Bench
的 RACE 框架（Comprehensiveness/Insight/Instruction-following/Readability），加了一条我们
自己更看重的"引用可信度"，配合 `judge.py` 做 LLM-judge 打分，用来在换模型/换 prompt 时做
主观对比——分数仅供参考，不作为硬性验收标准（硬性标准全部在 tests/eval 里，脚本判定）。

| 维度 | 权重 | 1 分 | 3 分 | 5 分 |
|---|---|---|---|---|
| 覆盖度 comprehensiveness | 25% | 只覆盖 1-2 个子问题 | 覆盖大部分子问题，个别浅 | checklist 每个子问题都有实质内容 |
| 洞察深度 insight | 25% | 罗列事实，无判断 | 有一些归纳，较浅 | 能交叉验证、指出分歧/趋势，不是简单堆信息 |
| 指令遵循 instruction-following | 20% | 明显跑题或漏掉硬性要求 | 基本符合要求 | 完全符合任务描述（含"不能编数字填缺口"等约束） |
| 可读性 readability | 15% | 结构混乱、语言生硬 | 结构清楚但表达一般 | 结构清晰、行文流畅、重点突出 |
| 引用可信度 citation trust | 15% | 引用稀疏或明显与内容不符 | 大部分结论有引用 | 关键结论均有引用，内部/外部数据来源区分清楚 |

## 用法

跑完一次 `deep_research_agent`，把报告另存一份带标识的文件名，换模型/prompt 再跑一次：

```bash
python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt --model deepseek/deepseek-v4-flash
cp workspace/output/research/专题报告.md workspace/output/research/专题报告_flash.md

python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt --model deepseek/deepseek-v4-pro
cp workspace/output/research/专题报告.md workspace/output/research/专题报告_pro.md

python -m deep_research_agent.judge workspace/output/research/专题报告_flash.md workspace/output/research/专题报告_pro.md
```
