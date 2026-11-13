"""dsh 大脑 —— DeepSeek Harness。

薄薄一层：把 Bus 收进来的消息翻译成 ``session`` 字符串，交给 DshBridge
（WebSocket 服务端，默认 3020），由 DSH 侧的 ``dsh-hub-bridge`` 插件调
``agent.followup()`` 开一轮，再把 ``turn/end`` 的最后一句传回来。

回复、超时、掉线兜底都在 DshBridge 里，这里只管「怎么把消息变成 session」。
"""

from __future__ import annotations

import logging

from adapters.dsh_bridge import DshBridge

from .base import Brain
from .registry import register

log = logging.getLogger("keihub.brains.dsh")

DEFAULTS = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 3020,
    "reply_timeout_seconds": 180,
    "fallback_to_bot": True,
}


@register
class DshBrain(Brain):
    name = "dsh"
    label = "DeepSeek Harness（dsh-hub-bridge）"

    def __init__(self, cfg: dict, bus):
        merged = {**DEFAULTS, **(cfg or {})}
        super().__init__(merged, bus)
        self.bridge = DshBridge(
            str(merged.get("host") or "127.0.0.1"),
            int(merged.get("port") or 3020),
            bus,
            reply_timeout=float(merged.get("reply_timeout_seconds") or 180),
            fallback_to_bot=bool(merged.get("fallback_to_bot", True)),
        )
        bus.bind_dsh(self.bridge)

    @property
    def connected(self) -> bool:
        return bool(self.bridge.connected)

    async def start(self):
        if not self.enabled:
            log.info("dsh 大脑未启用")
            return
        await self.bridge.start()

    async def stop(self):
        await self.bridge.stop()

    async def deliver(self, source: str, payload) -> bool:
        if source == "fatfish":
            msg = payload
            session = f"fatfish:{msg.channel}"
            ok = await self.bridge.send_inbound(
                session, msg.sender_name or msg.sender, msg.text, msg=msg
            )
            if ok:
                log.info("FatFish -> DSH: #%s %s: %s", msg.channel,
                         msg.sender_name, str(msg.text)[:80].replace("\n", " "))
            return ok

        if source == "onebot":
            event = payload
            mt = str(event.get("message_type") or "")
            if mt == "private":
                session = f"onebot:private:{event.get('user_id')}"
            elif mt == "group":
                session = f"onebot:group:{event.get('group_id')}"
            else:
                return False
            sender = event.get("sender") or {}
            name = str(sender.get("card") or sender.get("nickname")
                       or event.get("user_id") or "")
            text = self.bus._text_of(event.get("message"))
            if not text:
                return False
            ok = await self.bridge.send_inbound(session, name, text)
            if ok:
                log.info("SnowLuma -> DSH: %s %s", mt, event.get("user_id"))
            return ok

        return False
