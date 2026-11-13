import asyncio
import json
import logging
import time
import uuid

import websockets

log = logging.getLogger("keihub.dsh")

DEFAULT_REPLY_TIMEOUT = 180.0


class DshBridge:
    """Hub 对 DSH 极薄插件的 WebSocket 服务端。

    send_inbound() 只代表「帧发出去了」，不代表 DSH 真的会回。所以每条入站都
    登记一个 request_id：

    - 插件在 reply / error 里把 id 原样带回来，用它配对；
    - 报错、超时、插件掉线，都会走 on_dsh_failed 改投 momoi_bot，而不是静默丢。
    """

    def __init__(self, host: str, port: int, bus,
                 reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
                 fallback_to_bot: bool = True):
        self.host = host
        self.port = int(port)
        self.bus = bus
        self.clients = set()
        self.server = None
        self.reply_timeout = max(10.0, float(reply_timeout or DEFAULT_REPLY_TIMEOUT))
        self.fallback_to_bot = bool(fallback_to_bot)
        # request_id -> {"session", "msg", "deadline", "ws"}
        self.pending: dict[str, dict] = {}
        self._sweeper = None

    @property
    def connected(self) -> bool:
        return bool(self.clients)

    async def start(self):
        self.server = await websockets.serve(
            self._handler, self.host, self.port,
            ping_interval=None, max_size=None,
        )
        self._sweeper = asyncio.create_task(self._sweep(), name="dsh-reply-timeout")
        log.info("DSH bridge WS 服务已启动: ws://%s:%s（回复超时 %.0fs）",
                 self.host, self.port, self.reply_timeout)

    async def stop(self):
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    # ---------------- 回调配 ----------------
    def _take_job(self, data: dict):
        """配对一条入站：优先用插件回声的 id，旧版没带 id 就按 session 找最早的一条。"""
        rid = str(data.get("id") or "")
        if rid:
            job = self.pending.pop(rid, None)
            if job is not None:
                return job
        session = str(data.get("session") or "")
        if not session:
            return None
        for key, job in list(self.pending.items()):
            if job.get("session") == session:
                self.pending.pop(key, None)
                return job
        return None

    async def _failed(self, job: dict, reason: str):
        session = str((job or {}).get("session") or "")
        log.warning("DSH 未产生回复（%s）: %s", reason, session)
        if not self.fallback_to_bot:
            return
        try:
            await self.bus.on_dsh_failed(session, (job or {}).get("msg"), reason)
        except Exception:
            log.exception("改投 momoi_bot 失败: %s", session)

    async def _sweep(self):
        """定时把迟迟不回复的请求放行给 momoi_bot。"""
        while True:
            try:
                await asyncio.sleep(2)
                now = time.time()
                for rid, job in list(self.pending.items()):
                    if now < float(job.get("deadline") or 0):
                        continue
                    self.pending.pop(rid, None)
                    await self._failed(job, "超时")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("DSH 超时检查异常")

    # ---------------- 连接 ----------------
    async def _handler(self, ws):
        self.clients.add(ws)
        log.info("DSH 极薄插件已连接")
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                kind = data.get("type")
                if kind == "reply":
                    job = self._take_job(data)
                    session = str(data.get("session") or "")
                    text = str(data.get("text") or "").strip()
                    if session and text:
                        await self.bus.on_dsh_reply(session, text)
                elif kind == "error":
                    job = self._take_job(data)
                    await self._failed(job, str(data.get("message") or "插件报错"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("DSH 插件连接断开: %s", e)
        finally:
            self.clients.discard(ws)
            log.info("DSH 极薄插件已断开")
            # 这条连接上的请求永远不会回了，立刻改投而不是等到超时
            orphaned = [j for j in self.pending.values() if j.get("ws") is ws]
            for job in orphaned:
                for rid, cand in list(self.pending.items()):
                    if cand is job:
                        self.pending.pop(rid, None)
                await self._failed(job, "插件断开")

    async def send_inbound(self, session: str, user: str, text: str, msg=None) -> bool:
        if not self.clients:
            return False
        rid = uuid.uuid4().hex[:12]
        payload = json.dumps({
            "type": "inbound",
            "id": rid,
            "session": session,
            "user": user,
            "text": text,
        }, ensure_ascii=False)
        ok = False
        for ws in list(self.clients):
            try:
                await ws.send(payload)
                ok = True
                self.pending[rid] = {
                    "session": session,
                    "msg": msg,
                    "deadline": time.time() + self.reply_timeout,
                    "ws": ws,
                }
            except Exception as e:
                log.warning("发送给 DSH 插件失败: %s", e)
                self.clients.discard(ws)
        return ok
