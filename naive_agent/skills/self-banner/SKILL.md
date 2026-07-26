---
name: self-banner
description: >-
  在 workspace 里做一份可运行的 xagent 启动 ASCII 横幅 demo。
  触发场景：启动 banner、ASCII art、给自己加封面、终端封面。
disable-model-invocation: true
---

# 给自己加封面

## 约束

`read_file` / `write_file` / `run_shell` 只能碰 `workspace/`。不要尝试改仓库里的 `naive_agent/xagent.py`。

## 步骤

1. 在 `workspace/` 写一个小脚本（如 `xagent_banner.py`）：定义简易 "xagent" ASCII art，在入口 `print`。
2. `run_shell` 执行该脚本，确认横幅打印出来。
3. 回复里说明：生产系统不会让 agent 无约束改自己的运行代码；本练习刻意落在 workspace 沙箱内。
