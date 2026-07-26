1. 配置环境
【方法】
```bash
make setup
conda activate agent-from-scratch
```
编辑 `.env`，至少填 `DEEPSEEK_API_KEY`（其余平台按需）。`.env.example` 有注释。

【怎样知道已经完成】
- 终端打印 `环境已就绪，运行: conda activate agent-from-scratch`
- `conda activate agent-from-scratch` 后提示符带 `(agent-from-scratch)`
- 项目根目录有 `.env`

2. 冒烟测试
用来检验所需 API 连通性。未填 key 的平台会 skip，不算失败。
【方法】
```bash
make smoke
```
只测某一个：
```bash
pytest -m smoke tests/smoke/test_deepseek.py -v
```

【怎样知道已经完成】
- 已填 key 的用例 `PASSED`
- 未填的 `SKIPPED`
- 无 `FAILED`

3. 运行 agent
`--prompt` 可写任务文本，也可给 `prompts/*.txt` 路径。
`--model` 换模型；`--skill` 才挂 skills（默认不挂）。
产物在 `workspace/output/<agent>/`；清空用 `make co`。

- naive agent
```bash
python -m naive_agent.xagent --prompt "请介绍你自己"
```
现成任务见 `naive_agent/run.sh`（summarize / report / dashboard / snake / banner）。

- code agent
```bash
python -m code_agent.graph --prompt code_agent/prompts/dashboard.txt
```
详见 `code_agent/run.sh`。

- deep research agent
```bash
python -m deep_research_agent.graph --prompt deep_research_agent/prompts/cloud_report.txt
```
详见 `deep_research_agent/run.sh`。需要 Bocha key。

- gui agent（需视觉模型，默认 Qwen-VL）
```bash
python -m gui_agent.graph --max-steps 60
```
详见 `gui_agent/run.sh`。默认有头；`--headless` 不弹窗。启动时会先摘要
`snake.html` 源码（颜色/控制/tick），再按截图决策——naive_agent 重生一版也能适配。
