---
name: dashboard
description: >-
  把 workspace 里的结构化 CSV 做成简易数据大屏（HTML/PNG）。
  触发场景：数据大屏、可视化看板、CSV 画图、指标图表、plotly/seaborn。
disable-model-invocation: true
---

# 简易数据大屏

## 步骤

1. `run_shell` 确认有 pandas；按需 `pip install` seaborn 或 plotly。
2. 写脚本：读 CSV → 算 2~4 个核心指标 → 出图 → 存 HTML 或 PNG。
3. 一页看清大盘趋势、厂商份额即可，别贪多图。
4. 标题与坐标轴写清单位与口径（人民币/美元、年/半年）。
5. `run_shell` 跑通脚本，确认产物文件已生成。
