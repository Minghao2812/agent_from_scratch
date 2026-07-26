"""贪吃蛇场景的浏览器控制层：CDP 虚拟时间冻结 + JS 事件按键派发。

对照 webdev-arena/cdp_verifier（verifier.py / cdp_controller.py / action.py）的取舍：

保留（原样复用，都是真实踩过的坑，不是猜的）：
- CDP ``Emulation.setVirtualTimePolicy`` 冻结/推进虚拟时间：VLM 决策要 2~5 秒，游戏
  tick 只有 150ms，不冻结时间的话截图内容和最终决策永远对不上。
- CDP ``Page.captureScreenshot`` 代替 ``page.screenshot()``：后者会等 web 字体加载完
  才返回，虚拟时间暂停时字体请求依赖真实时间，永远等不完，直接卡到超时。
- 按键走 ``page.evaluate()`` 派发 JS ``KeyboardEvent``，不用 ``page.keyboard.press()``：
  后者经 CDP Input pipeline，会触发 compositor 的 paint 请求，但虚拟时间暂停中
  compositor 完不成合成，导致下一次 ``Page.captureScreenshot`` 永久挂起（cdp_verifier
  docs/PITFALLS.md #8 有完整复现记录）。keydown/keyup 分离、keyup 延迟到下一步开始才
  释放，是为了让 ``nextDirection`` 这类"记录最近一次按键"的游戏逻辑能读到状态。

简化（原项目的复杂度服务于"给任意未知前端打分"，这里只服务于内部这一个游戏）：
- 不做 CDP session 失效后的新页面 recovery：那是应对被测应用内部调用
  ``location.reload()``（如原项目测试的 game-over 自动重开）。当前 snake.html 的
  game-over 只是画一层遮罩，不调用 reload，恢复逻辑没有对应的失效场景。
- 不做 CDN 镜像/缓存：snake.html 是单文件离线页面，没有外部资源请求。
- 不做鼠标点击：游戏本身只吃键盘输入，开局按钮由 harness 直接调 JS 函数触发
  （见 graph.py），不算 agent 决策的一部分。
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import CDPSession, Page


class CDPController:
    """虚拟时间冻结/推进 + 截图，三个方法对应 verify 循环的三个基本操作。"""

    def __init__(self, cdp_session: CDPSession):
        self.cdp = cdp_session

    async def pause(self) -> bool:
        try:
            await self.cdp.send("Emulation.setVirtualTimePolicy", {"policy": "pause"})
            return True
        except Exception:
            return False

    async def resume(self, budget_ms: int) -> bool:
        """推进 budget_ms 毫秒虚拟时间，Chromium 会在预算耗尽后自动回到 pause 状态。"""
        try:
            await self.cdp.send(
                "Emulation.setVirtualTimePolicy", {"policy": "advance", "budget": budget_ms}
            )
            return True
        except Exception:
            return False

    async def screenshot(self, output_path: str, timeout: float = 8.0) -> Optional[str]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = await asyncio.wait_for(
                self.cdp.send("Page.captureScreenshot", {"format": "png"}), timeout=timeout
            )
            path.write_bytes(base64.b64decode(result["data"]))
            return str(path)
        except Exception:
            return None


# snake.html 只认这四个方向键（见 handleKeydown），不需要 cdp_verifier 里那一整张
# 覆盖功能键/组合键的别名表——多出来的映射规则没有对应的实际用例。
_KEY_MAP = {
    "arrowup": "ArrowUp", "up": "ArrowUp",
    "arrowdown": "ArrowDown", "down": "ArrowDown",
    "arrowleft": "ArrowLeft", "left": "ArrowLeft",
    "arrowright": "ArrowRight", "right": "ArrowRight",
}

_JS_KEY_OPTS = """
    const kc = {ArrowUp:38,ArrowDown:40,ArrowLeft:37,ArrowRight:39}[key] || 0;
    const opts = {key, keyCode: kc, which: kc, bubbles: true, cancelable: true};
"""
_JS_KEYDOWN = "(key) => {" + _JS_KEY_OPTS + "window.dispatchEvent(new KeyboardEvent('keydown', opts));}"
_JS_KEYUP = "(key) => {" + _JS_KEY_OPTS + "window.dispatchEvent(new KeyboardEvent('keyup', opts));}"


def normalize_key(s: str) -> Optional[str]:
    """把 VLM 输出的方向词归一化成 snake.html 认识的 Arrow 键名，识别失败返回 None。"""
    key = re.sub(r"[\s_-]", "", s.strip().lower())
    return _KEY_MAP.get(key)


class ActionExecutor:
    """按键派发：keydown 立即发出，keyup 延迟到下一次 press() 开始时才释放（跨越整个
    resume 周期，让基于 setInterval/setTimeout 读取按键状态的游戏循环能感知到这次按键）。
    """

    def __init__(self):
        self._held_key: str | None = None

    async def _release(self, page: Page) -> None:
        if self._held_key:
            await page.evaluate(_JS_KEYUP, self._held_key)
            self._held_key = None

    async def press(self, page: Page, key: str) -> None:
        await self._release(page)
        await page.evaluate(_JS_KEYDOWN, key)
        self._held_key = key
