import asyncio
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from core.config import (
    DEFAULT_FATFISH_BASE,
    discover_fatfish_base,
    resolve_fatfish_token,
)
from core.models import Inbound

log = logging.getLogger("keihub.fatfish")


class FatFishAdapter:
    def __init__(self, cfg: dict, state, bus):
        self.cfg = cfg
        self.state = state
        self.bus = bus
        self.fat = cfg.get("adapters", {}).get("fatfish", {}) or {}
        self.configured_base = str(self.fat.get("api_base") or DEFAULT_FATFISH_BASE).rstrip("/")
        self.api_base = self.configured_base
        self._base_checked = 0.0
        self.db_path = str(self.fat.get("db_path") or "")
        self.presence_path = str(self.fat.get("presence_db_path") or "")
        self.bot_username = str(self.fat.get("bot_username") or "kei_bot")
        self.poll = max(0.5, float(self.fat.get("poll_seconds") or 2.0))
        self.send_mode = str(self.fat.get("send_mode") or "auto").lower()
        mb = self.fat.get("max_backlog_seconds", 900)
        try:
            self.max_backlog = max(0, int(900 if mb is None else mb))
        except Exception:
            self.max_backlog = 900
        self.start_from = str(self.fat.get("start_from") or "latest").lower()
        self.enabled = bool(self.fat.get("enabled", True))
        self.auto_channels = bool(self.fat.get("auto_channels", True))
        self.skip_senders = self._skip_senders()
        self.active_channels: dict[str, dict] = {}
        self._discovered = False
        self._auto_channels: set[str] = set()
        self.token = resolve_fatfish_token(cfg)
        self.channels = self.fat.get("channels") or {}
        self.alice_ids: set[int] = set()
        for raw_id in self.fat.get("alice_user_ids") or [3337894736]:
            try:
                self.alice_ids.add(int(raw_id))
            except Exception:
                pass
        self._warned_channels = set()
        self._last_prune = 0.0

    # ---------------- 后端地址 ----------------
    def _skip_senders(self) -> set[str]:
        """哪些 sender 算「自己发的」，读到就当回声跳过。

        bot_username 只是写库时用的名字；用 token 走 API 时真实发送者是 token 的
        持有者（可能是另一个账号），所以额外用 skip_senders 补上。
        """
        raw = self.fat.get("skip_senders")
        names = [str(x) for x in raw] if isinstance(raw, (list, tuple)) else []
        names.append(str(self.fat.get("bot_username") or "kei_bot"))
        return {n.strip().casefold() for n in names if n and n.strip()}

    def server_log_path(self) -> str:
        raw = str(self.fat.get("server_log_path") or "").strip()
        if raw:
            return raw
        if self.db_path:
            try:
                return str(Path(self.db_path).parent.parent / "server.log")
            except Exception:
                pass
        return ""

    async def _ensure_base(self, force: bool = False) -> str:
        """FatFish 每次启动可能换端口，发消息前确认一下当前真实地址。

        后端关着的时候探测会走超时，所以丢到线程里，别卡住事件循环。
        """
        now = time.time()
        if not force and self.api_base and now - self._base_checked < 30:
            return self.api_base
        self.api_base = await asyncio.to_thread(
            discover_fatfish_base,
            self.configured_base, self.server_log_path(), force, 0.4, 2.0,
        )
        self._base_checked = time.time()
        return self.api_base

    def apply_config(self, fat: dict):
        """控制台粘贴 API 地址后热更新，不用重启 Hub。"""
        old_channels = list(self.channels)
        was_enabled = self.enabled
        self.fat = fat or {}
        self.configured_base = str(self.fat.get("api_base") or DEFAULT_FATFISH_BASE).rstrip("/")
        self.api_base = self.configured_base
        self._base_checked = 0.0
        self.token = resolve_fatfish_token({"adapters": {"fatfish": self.fat}})
        self.send_mode = str(self.fat.get("send_mode") or "auto").lower()
        self.bot_username = str(self.fat.get("bot_username") or "kei_bot")
        self.db_path = str(self.fat.get("db_path") or "")
        self.presence_path = str(self.fat.get("presence_db_path") or "")
        self.enabled = bool(self.fat.get("enabled", True))
        self.auto_channels = bool(self.fat.get("auto_channels", True))
        self.skip_senders = self._skip_senders()
        self._discovered = False
        self._auto_channels = set()
        try:
            self.poll = max(0.5, float(self.fat.get("poll_seconds") or 2.0))
        except Exception:
            pass
        mb = self.fat.get("max_backlog_seconds", 900)
        try:
            self.max_backlog = max(0, int(900 if mb is None else mb))
        except Exception:
            self.max_backlog = 900

        self.alice_ids = set()
        for raw_id in self.fat.get("alice_user_ids") or [3337894736]:
            try:
                self.alice_ids.add(int(raw_id))
            except Exception:
                pass

        self.channels = self.fat.get("channels") or {}
        if list(self.channels) != old_channels:
            # 新频道没有游标，从「最新」开始，避免把历史全回复一遍
            try:
                self._init_cursors()
            except Exception:
                log.warning("重建 FatFish 游标失败", exc_info=True)

        if not was_enabled and self.enabled:
            log.warning("FatFish 适配器已改为启用，但轮询循环早已退出，需重启 Hub")
        log.info("FatFish 配置已热更新：api_base=%s token=%s send_mode=%s channels=%s",
                 self.api_base, "已配置" if self.token else "无",
                 self.send_mode, list(self.channels))

    # ---------------- 生命周期 ----------------
    async def run(self):
        if not self.enabled:
            log.info("FatFish 适配器未启用")
            return
        self._init_cursors()
        log.info("FatFish 轮询已启动：db=%s，channels=%s", self.db_path, list(self.channels))
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("FatFish 轮询异常")
            await asyncio.sleep(self.poll)

    def _connect_read(self):
        uri = "file:" + Path(self.db_path).as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _connect_write(self, path: str):
        conn = sqlite3.connect(str(path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_cursors(self):
        conn = self._connect_read()
        try:
            for channel in self.channels:
                cur = self.state.get_cursor("fatfish", channel)
                if cur:
                    continue
                row = conn.execute(
                    "SELECT COALESCE(MAX(id),0) FROM messages WHERE channel=?",
                    (channel,),
                ).fetchone()
                last = int(row[0] or 0)
                if self.start_from == "latest":
                    self.state.set_cursor("fatfish", channel, last)
                    log.info("FatFish 频道 %s 初始游标=%s", channel, last)
        finally:
            conn.close()

    def _discover_channels(self, conn) -> list[str]:
        """发现 messages 表里的频道。

        FatFish 每新建一个私聊/群就会造一个新频道名（dm:a:b / private:N），
        写死在 config 里必然漏。首次调用把现有频道全捞一遍，之后按全局游标增量。
        """
        if not self.auto_channels:
            return []
        try:
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()
            max_id = int(row[0] or 0)
            if not self._discovered:
                # 每个进程的首轮：完整扫一次，之后靠全局游标增量
                self._discovered = True
                rows = conn.execute("SELECT DISTINCT channel FROM messages").fetchall()
            else:
                cursor = self.state.get_cursor("fatfish", "*")
                if max_id <= cursor:
                    return []
                rows = conn.execute(
                    "SELECT DISTINCT channel FROM messages WHERE id > ?", (cursor,)
                ).fetchall()
            self.state.set_cursor("fatfish", "*", max_id)
            return [str(r[0]) for r in rows if r[0]]
        except Exception:
            log.warning("FatFish 频道自动发现失败", exc_info=True)
            return []

    async def poll_once(self):
        await self._ensure_base()
        now = time.time()
        if now - self._last_prune > 600:
            self._last_prune = now
            try:
                self.state.prune_seen()
            except Exception:
                log.warning("清理 seen 表失败", exc_info=True)
        conn = self._connect_read()
        try:
            # 配置里写死的频道 + 自动发现的频道（dm:xxx / private:N / group:N）
            active: dict[str, dict] = dict(self.channels)
            for channel in self._discover_channels(conn):
                if channel in self._auto_channels:
                    continue
                self._auto_channels.add(channel)
                if channel in active:
                    continue
                meta = self.bus.resolve_channel(channel)
                if meta:
                    log.info("FatFish 自动映射频道 %s -> %s", channel, meta)
            # 首轮之后 _discover_channels 只返回新增的，靠这个集合维持轮询
            for channel in self._auto_channels:
                if channel in active:
                    continue
                meta = self.bus.resolve_channel(channel)
                if meta:
                    active[channel] = meta
            self.active_channels = active
            for channel, meta in active.items():
                if not isinstance(meta, dict):
                    continue
                cursor = self.state.get_cursor("fatfish", channel)
                rows = conn.execute(
                    "SELECT id,channel,sender,content,content_type,ts "
                    "FROM messages WHERE channel=? AND id>? AND deleted=0 "
                    "ORDER BY id ASC LIMIT 50",
                    (channel, cursor),
                ).fetchall()
                for row in rows:
                    mid = int(row["id"])
                    sender = str(row["sender"] or "")
                    content = str(row["content"] or "").strip()
                    ctype = str(row["content_type"] or "text")
                    ts = float(row["ts"] or 0)
                    if self.state.seen("fatfish_out", f"{channel}:{mid}"):
                        self.state.set_cursor("fatfish", channel, mid)
                        continue
                    if self.state.seen("fatfish", f"{channel}:{mid}"):
                        self.state.set_cursor("fatfish", channel, mid)
                        continue
                    if (
                        not content
                        or ctype != "text"
                        or content.startswith(("[sticker:", "[image:", "[file:", "[video:", "[voice:"))
                        or sender.casefold() in self.skip_senders
                        or sender.casefold() == "system"
                    ):
                        self.state.set_cursor("fatfish", channel, mid)
                        continue
                    if self.max_backlog and ts and time.time() - ts > self.max_backlog:
                        self.state.set_cursor("fatfish", channel, mid)
                        continue
                    is_group = str(meta.get("type", "group")).lower() == "group"
                    try:
                        group_id = int(meta.get("group_id") or 0)
                    except Exception:
                        group_id = 0
                    display = sender
                    if sender in (self.fat.get("users") or {}):
                        uid = int((self.fat.get("users") or {}).get(sender) or 0)
                        if uid in self.alice_ids:
                            display = "爱丽丝"
                    msg = Inbound(
                        platform="fatfish",
                        channel=str(channel),
                        sender=sender,
                        sender_name=display,
                        text=content,
                        native_id=str(mid),
                        ts=ts,
                        is_group=is_group,
                        group_id=group_id,
                    )
                    try:
                        ok = await self.bus.on_fatfish(msg)
                    except Exception:
                        log.exception("FatFish 消息路由失败 id=%s", mid)
                        break
                    if ok is False:
                        log.warning("bot 未连接，保留 FatFish 游标等待重试")
                        break
                    self.state.set_cursor("fatfish", channel, mid)
        finally:
            conn.close()

    # ---------------- 发送 ----------------
    async def send_to_channel(self, channel: str, text: str):
        mode = self.send_mode
        if mode == "db" or (mode != "api" and not self.token):
            result = await asyncio.to_thread(self._send_db, channel, text)
        else:
            try:
                result = await self._send_api(channel, text)
            except Exception as e:
                log.warning("FatFish API 发送失败: %s，尝试直写数据库", e)
                result = await asyncio.to_thread(self._send_db, channel, text)
        if result:
            self.state.mark_seen("fatfish_out", f"{channel}:{result}")
        return result

    # ---------------- 查历史消息 ----------------
    def get_message(self, channel: str, native_id: str):
        """按 FatFish 原始 id 取一条消息，供 Hub 本地回答 get_msg。"""
        try:
            mid = int(str(native_id))
        except Exception:
            return None
        conn = self._connect_read()
        try:
            return conn.execute(
                "SELECT id, channel, sender, content, content_type, ts "
                "FROM messages WHERE id=? AND channel=?",
                (mid, str(channel)),
            ).fetchone()
        except Exception:
            log.warning("读取 FatFish 消息失败 id=%s", native_id, exc_info=True)
            return None
        finally:
            conn.close()

    async def _send_api(self, channel: str, text: str):
        if not self.token:
            raise RuntimeError("FatFish token 未配置")
        base = await self._ensure_base()
        try:
            return await asyncio.to_thread(self._post_api, base, channel, text)
        except Exception as e:
            # 端口漂移是最常见的失败原因：强制重新探测后再试一次
            log.warning("FatFish API 失败（%s）: %s，重新探测端口后重试", base, e)
            base = await self._ensure_base(force=True)
            return await asyncio.to_thread(self._post_api, base, channel, text)

    def _post_api(self, base: str, channel: str, text: str):
        body = json.dumps({"channel": channel, "text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            str(base).rstrip("/") + "/api/v1/messages",
            data=body, method="POST",
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        msg = data.get("message") or data.get("data") or {}
        if isinstance(msg, dict):
            return int(msg.get("id") or 0)
        return 0

    def _send_db(self, channel: str, text: str):
        db = Path(self.db_path)
        if not db.exists():
            raise RuntimeError(f"FatFish 消息库不存在: {db}")
        sender = self.bot_username or "kei_bot"
        now = time.time()
        client_id = uuid.uuid4().hex
        conn = self._connect_write(str(db))
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO messages(channel,sender,content,content_type,ts,edited,deleted,client_msg_id) "
                "VALUES(?,?,?,'text',?,0,0,?)",
                (channel, sender, text, now, client_id),
            )
            mid = cur.lastrowid
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        presence_raw = str(self.presence_path or "").strip()
        presence = Path(presence_raw) if presence_raw else db.parent.parent / "presence.db"
        if presence.exists():
            try:
                p = self._connect_write(str(presence))
                cur = p.execute("UPDATE event_signal SET gen=gen+1 WHERE id=1")
                if cur.rowcount == 0:
                    p.execute("INSERT INTO event_signal(id,gen) VALUES(1,1)")
                p.commit()
                p.close()
            except Exception as e:
                log.warning("推进 FatFish event_signal 失败: %s", e)
        return int(mid or 0)
