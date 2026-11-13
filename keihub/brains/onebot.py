"""onebot 大脑 —— momoi_bot，也就是原来的默认链路。

它不实现 deliver：Bus 见到 ``onebot`` 就直接走内联的 ``_route_to_bot`` /
``bot.send_event``，这样默认行为和老版本逐字节一致，没有回归风险。

注册在这里只是为了两件事：
1. 控制台能列出「有哪些脑子可选」；
2. 配置写 ``fatfish_brain: "bot"`` 时能被 resolve 成 ``onebot``。
"""

from __future__ import annotations

from .base import Brain
from .registry import register


@register
class OneBotBrain(Brain):
    name = "onebot"
    label = "momoi_bot（OneBot 11，默认）"
    is_default = True

    @property
    def enabled(self) -> bool:
        # 由 adapters.bot.enabled 控制，不看 brains 段
        return True

    @property
    def connected(self) -> bool:
        server = getattr(self.bus, "bot", None)
        return bool(server is not None and getattr(server, "ws", None) is not None)
