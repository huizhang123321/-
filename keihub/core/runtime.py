import asyncio
import time
from pathlib import Path
from typing import Any

from .config import BASE_DIR


class HubRuntime:
    """给 Web 控制台/托盘读取的运行状态，以及跨线程调用协程的入口。"""

    def __init__(self):
        self.started_at = time.time()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot = None
        self.snow = None
        self.fatfish = None
        self.dsh = None
        self.brains: dict = {}
        self.bus = None
        self.state = None
        self.log_path = BASE_DIR / "logs" / "hub.log"

    def run_coro(self, coro, timeout: float = 30):
        if self.loop is None:
            raise RuntimeError("Hub 事件循环尚未启动")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout)

    def status(self) -> dict[str, Any]:
        bot_connected = bool(self.bot is not None and getattr(self.bot, "ws", None) is not None)
        snow_connected = bool(self.snow is not None and getattr(self.snow, "ws", None) is not None)
        dsh_connected = bool(self.dsh is not None and getattr(self.dsh, "connected", False))
        channels = []
        if self.fatfish is not None:
            # 优先显示实际在轮询的频道（含自动发现的），而不是配置里那两条
            active = getattr(self.fatfish, "active_channels", None)
            if active:
                channels = list(active.keys())
            else:
                channels = list((getattr(self.fatfish, "channels", {}) or {}).keys())
        fat_base = ""
        fat_mode = ""
        fat_token = False
        if self.fatfish is not None:
            fat_base = str(getattr(self.fatfish, "api_base", "") or "")
            fat_mode = str(getattr(self.fatfish, "send_mode", "") or "")
            fat_token = bool(getattr(self.fatfish, "token", ""))
        brains = {}
        if self.bus is not None:
            brains = {
                "fatfish": getattr(self.bus, "fatfish_brain", "onebot"),
                "onebot": getattr(self.bus, "onebot_brain", "onebot"),
            }
        brain_list = []
        try:
            from brains import describe as describe_brains

            brain_list = describe_brains(self.brains)
        except Exception:
            brain_list = []
        return {
            "ok": True,
            "started_at": self.started_at,
            "uptime_seconds": max(0, int(time.time() - self.started_at)),
            "bot_connected": bot_connected,
            "snow_connected": snow_connected,
            "dsh_connected": dsh_connected,
            "fatfish_channels": channels,
            "fatfish_api_base": fat_base,
            "fatfish_send_mode": fat_mode,
            "fatfish_token_set": fat_token,
            "brains": brains,
            "brain_list": brain_list,
            "channel_brains": dict(getattr(self.bus, "channel_brains", {}) or {}),
            "log_path": str(self.log_path),
        }
