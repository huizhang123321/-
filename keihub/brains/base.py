"""所有「大脑」的基类。

一个 brain 只需要回答三件事：

1. ``connected`` —— 现在能不能接活（掉线时 Bus 会自动回落默认脑）
2. ``deliver(source, payload)`` —— 把一条入站消息交给它，返回是否已接管
3. ``start() / stop()`` —— 自己的生命周期（监听端口、建长连接等）

``source`` 目前有两种：

    "fatfish"   payload 是 core.models.Inbound
    "onebot"    payload 是 OneBot 11 的原始事件 dict

回复走的是 ``bus.on_brain_reply(session, text)``，session 的格式由 brain 自己定，
Hub 按前缀还原到平台（见 core/bus.py 的 on_brain_reply）。
"""

from __future__ import annotations

import logging

log = logging.getLogger("keihub.brains")


class Brain:
    """大脑基类。子类至少覆盖 name / label 和 deliver。"""

    name = "base"
    label = "基类"

    #: 是否算「默认链路」。默认脑由 Bus 内联处理，不走 deliver。
    is_default = False

    def __init__(self, cfg: dict, bus):
        self.cfg = cfg or {}
        self.bus = bus

    # ---------------- 状态 ----------------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    @property
    def connected(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
            "connected": self.connected,
            "is_default": self.is_default,
        }

    # ---------------- 生命周期 ----------------
    async def start(self):
        """子类按需覆盖。"""

    async def stop(self):
        """子类按需覆盖。"""

    # ---------------- 收消息 ----------------
    async def deliver(self, source: str, payload) -> bool:
        """接管这条消息返回 True；返回 False 则 Bus 回落默认脑。"""
        return False

    # ---------------- 调试 ----------------
    def __repr__(self) -> str:
        return f"<Brain {self.name} connected={self.connected}>"
