import asyncio
import json
import logging
import time

import websockets

from . import snowluma_http

log = logging.getLogger("keihub.snow_ws")


class SnowLumaWS:
    """Hub 作为客户端连接 SnowLuma 的正向 WebSocket。"""

    def __init__(self, cfg: dict, bus):
        self.cfg = cfg or {}
        self.bus = bus
        self.url = str(self.cfg.get("ws_url") or "ws://127.0.0.1:3011")
        self.http_url = str(self.cfg.get("http_url") or "http://127.0.0.1:3012")
        self.token = str(self.cfg.get("token") or "")
        self.reconnect = max(1, int(self.cfg.get("reconnect_seconds", 5) or 5))
        self.enabled = bool(self.cfg.get("enabled", True))
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()

    async def run(self):
        if not self.enabled:
            log.info("SnowLuma 适配器未启用")
            return
        while True:
            try:
                async with websockets.connect(
                    self.url, ping_interval=None, max_size=None
                ) as ws:
                    self.ws = ws
                    self.bus.bind_snow(self)
                    log.info("已连接 SnowLuma 正向 WS: %s", self.url)
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue
                        await self._on_data(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("SnowLuma WS 断开/连接失败: %s（%ss 后重试）", e, self.reconnect)
            finally:
                self.ws = None
            await asyncio.sleep(self.reconnect)

    async def _on_data(self, data: dict):
        echo = data.get("echo")
        if echo is not None and "post_type" not in data:
            fut = self.pending.get(str(echo))
            if fut and not fut.done():
                fut.set_result(data)
            return
        if data.get("post_type"):
            try:
                await self.bus.on_snow_event(data)
            except Exception:
                log.exception("处理 SnowLuma 事件失败")

    async def send_action(self, action: str, params: dict, echo=None, timeout: int = 12):
        if not self.enabled:
            return {"status": "failed", "retcode": 100, "data": None,
                    "message": "SnowLuma 适配器未启用", "echo": echo}
        if not echo:
            echo = f"hub_{action}_{int(time.time() * 1000)}"
        echo = str(echo)

        if self.ws is None:
            return snowluma_http.post_action(
                self.http_url, action, params, self.token, echo, timeout
            )

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[echo] = fut
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps(
                    {"action": action, "params": params or {}, "echo": echo},
                    ensure_ascii=False,
                ))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("SnowLuma action 超时: %s", action)
            return {"status": "failed", "retcode": 120, "data": None,
                    "message": "timeout", "echo": echo}
        except Exception as e:
            log.warning("SnowLuma action 失败 %s: %s，尝试 HTTP 兜底", action, e)
            return snowluma_http.post_action(
                self.http_url, action, params, self.token, echo, timeout
            )
        finally:
            self.pending.pop(echo, None)
