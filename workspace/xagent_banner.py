#!/usr/bin/env python3
"""xagent 终端启动横幅 — 参考 Claude Code CLI 风格"""

import shutil
import os
import sys


def _term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return default


def main():
    tw = _term_width()

    # ── 颜色（尊重 NO_COLOR 与非 TTY） ──
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        RESET = BOLD = DIM = ""
        CYAN = GREEN = YELLOW = BLUE = MAGENTA = WHITE = ""
    else:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
        WHITE = "\033[37m"

    # ═══════════════════════════════════════════════════════════════
    #  Logo — FIGlet "ANSI Shadow" 风格
    # ═══════════════════════════════════════════════════════════════
    logo_lines = [
        f"{CYAN}{BOLD}  ██╗  ██╗   █████╗   ██████╗  ███████╗ ███╗   ██╗ ████████╗",
        f"{CYAN}{BOLD}  ╚██╗██╔╝  ██╔══██╗ ██╔════╝  ██╔════╝ ████╗  ██║ ╚══██╔══╝",
        f"{CYAN}{BOLD}   ╚███╔╝   ███████║ ██║  ███╗ █████╗   ██╔██╗ ██║    ██║   ",
        f"{CYAN}{BOLD}   ██╔██╗   ██╔══██║ ██║   ██║ ██╔══╝   ██║╚██╗██║    ██║   ",
        f"{CYAN}{BOLD}  ██╔╝ ██╗  ██║  ██║ ╚██████╔╝ ███████╗ ██║ ╚████║    ██║   ",
        f"{CYAN}{BOLD}  ╚═╝  ╚═╝  ╚═╝  ╚═╝  ╚═════╝  ╚══════╝ ╚═╝  ╚═══╝    ╚═╝   ",
    ]

    print()
    for line in logo_lines:
        print(line)
    print(RESET, end="")

    # ── 分隔线 ──
    sep = "─" * min(tw, 72)
    print(f"{DIM}{sep}{RESET}")

    # ── 信息行 ──
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    info = [
        f"{BOLD}xagent{WHITE} v0.1.0{RESET}  {DIM}— coding agent, sandboxed{RESET}",
        "",
        f"  {GREEN}●{RESET} 模型：{YELLOW}deepseek-v3.1{RESET}   {DIM}│{RESET}   {GREEN}●{RESET} 上下文：{YELLOW}128K tokens{RESET}",
        f"  {BLUE}●{RESET} 工作目录：{BOLD}{cwd}{RESET}",
        f"  {BLUE}●{RESET} 沙箱模式：{GREEN}已启用{RESET}  {DIM}(读写仅限沙箱根目录){RESET}",
        "",
        f"  {DIM}输入 {RESET}/help{DIM} 查看可用命令{RESET}   {DIM}│{RESET}   {DIM}输入 {RESET}exit{DIM} 或 {RESET}Ctrl+C{DIM} 退出{RESET}",
    ]

    for line in info:
        print(line)

    # ── 底部分隔 ──
    sep2 = "─" * min(tw, 72)
    print(f"{DIM}{sep2}{RESET}")
    print()


if __name__ == "__main__":
    main()
