---
name: dashboard
description: >-
  把 workspace 里的结构化 CSV 做成单一、能立刻打开的 HTML 数据看板（纯 HTML/CSS，无重型图表库）。
  触发场景：经营数据看板、可视化大屏、CSV 画图、指标可重算校验。
disable-model-invocation: true
---

# 做一份单一 HTML 数据看板（要能立刻打开）

## 步骤

1. 读 CSV，算 4–6 个最有信息量的图所需数据。
2. 写 `build_dashboard.py`，生成**唯一** `dashboard.html`。
3. 再写独立 `verify.py` 重算关键数字并 assert，跑通才算完成。

## 视觉规范

- 深色渐变 header + KPI 数字条 + 卡片网格。
- 单位不一致（亿元 vs 百万美元）必须显著标注；缺口数据不要编。

## 技术要点：优先纯 HTML/CSS，禁止重型方案

下面这些都试过、都会「一直转圈」或打不开，**不要用**：

| 方案 | 失败方式 |
|------|----------|
| Plotly CDN | 断网阻塞 |
| Plotly inline / 旁路 `plotly.min.js` | 数 MB / 多文件，IDE 假死 |
| matplotlib → 整行 base64 `<img>` | 单行 10 万+ 字符，Cursor 打开一直转圈 |

**推荐：纯 HTML + CSS 条形图**（`<div>` 设宽度百分比）。文件通常 < 50KB，编辑器和浏览器都能秒开。

允许加**少量原生 JS**做交互（悬停高亮、鼠标跟随 tooltip、入场动画），不要上 React/JSX，
也不要引入 Plotly/ECharts 等重型库。

```html
<div class="bar-row tip" data-tip="阿里云：3698 百万美元">
  <div class="bar-label">阿里云</div>
  <div class="bar-track"><div class="bar-fill" style="--w:85%"></div></div>
  <div class="bar-val">3698</div>
</div>
```

自检：

```bash
# 单一文件、无外链、无旁路 js、无超长行
test -f dashboard.html && test ! -f plotly.min.js
grep -E 'https?://' dashboard.html          # 应无输出（或仅注释）
python3 -c "print(max(map(len, open('dashboard.html'))) )"  # 应远小于 5000
```

关键数字写进页面正文（KPI / 脚注），并嵌入 `const METRICS = {...}` 供 verify.py 核对。
