#!/bin/bash

# 默认有头运行，会弹出浏览器窗口
python -m gui_agent.graph --max-steps 60
# python -m gui_agent.graph --max-steps 60 --headless   # 跑 eval/CI 时不弹窗口
