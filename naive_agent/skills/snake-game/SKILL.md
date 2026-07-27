---
name: snake-game
description: >-
  写可在浏览器打开的单文件贪吃蛇（HTML/JS + canvas）。
  触发场景：贪吃蛇、snake、网页小游戏、canvas 游戏、给 GUI agent 当操作靶。
---

# 贪吃蛇（单文件 HTML）

## 步骤

1. 单个 HTML：内嵌 `<canvas>` + `<script>`，无外部依赖、不拆多文件。
2. 必备：方向键控制、吃食物变长、撞墙/撞自己结束、分数、结束后可重开。
3. 关键控件给稳定 `id`（开始/重开/分数区），便于后续 DOM 或坐标操作。
4. `run_shell` 起本地静态服务能打开该文件即完成（人工打开确认即可）。
