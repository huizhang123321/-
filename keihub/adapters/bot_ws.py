import asyncio
import json
import logging

import websockets

log = logging.getLogger("keihub.bot_ws")


class BotWSServer:
    """对 momoi_bot 暴露 OneBot v11 WebSocket 服务端。"""

    def __init__(self, host: str, port: int, bus):
        self.host = host
        self.port = int(port)
        self.bus = bus
        self.ws = None
        self.server = None
        self._send_lock = asyncio.Lock()

    async def start(self):
        self.server = await websockets.serve(
            self._handler, self.host, self.port,
            ping_interval=None, max_size=None,
        )
        log.info("bot WS 服务已启动: ws://%s:%s", self.host, self.port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handler(self, ws):
        self.ws = ws
        log.info("momoi_bot 已连接")
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if "action" not in data:
                    continue
                resp = await self.bus.on_bot_action(
                    str(data.get("action") or ""),
                    data.get("params") or {},
                    data.get("echo"),
                )
                try:
                    await ws.send(json.dumps(resp, ensure_ascii=False))
                except Exception:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("bot WS 断开: %s", e)
        finally:
            if self.ws is ws:
                self.ws = None
            log.info("momoi_bot 已断开")

    async def send_event(self, event: dict):
        if self.ws is None:
            log.warning("bot 未连接，丢弃事件: %s", event.get("message_type"))
            return False
        async with self._send_lock:
            try:
                await self.ws.send(json.dumps(event, ensure_ascii=False))
                return True
            except Exception as e:
                log.warning("发送事件到 bot 失败: %s", e)
                return False
