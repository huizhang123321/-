import json
import logging
import time
from typing import Optional

from .models import Inbound

log = logging.getLogger("keihub.bus")


class HubBus:
    """把 FatFish / SnowLuma / DSH 的消息路由到 momoi_bot，再把 bot 的 action 路由回去。"""

    def __init__(self, cfg: dict, state):
        self.cfg = cfg
        self.state = state
        self.bot = None
        self.snow = None
        self.fatfish = None
        self.dsh = None

        adapters = cfg.get("adapters", {}) or {}
        self.bot_cfg = adapters.get("bot", {}) or {}
        self.fat_cfg = adapters.get("fatfish", {}) or {}
        self.snow_cfg = adapters.get("snowluma", {}) or {}
        routing = cfg.get("routing", {}) or {}
        self.fatfish_brain = str(
            routing.get("fatfish_brain") or routing.get("default_brain") or "bot"
        ).lower()
        self.onebot_brain = str(
            routing.get("onebot_brain") or routing.get("default_brain") or "bot"
        ).lower()

        self.bot_qq = int(self.bot_cfg.get("bot_qq") or 3780075894)
        self.inject_at = bool(self.bot_cfg.get("inject_at", True))
        self.route_window = max(
            5, int((cfg.get("hub", {}) or {}).get("private_route_window_seconds", 45) or 45)
        )

        self.channel_to_group: dict[str, int] = {}
        self.group_to_channel: dict[int, str] = {}
        self.user_map: dict[str, int] = {}
        self.alice_ids: set[int] = set()
        self.auto_channels = bool(self.fat_cfg.get("auto_channels", True))
        self._build_fatfish_maps()

        # user_id -> (platform, channel, expires_at)
        self.private_route: dict[int, tuple[str, str, float]] = {}
        self._next_synthetic = 900000100
        self._load_auto_channels()

    def _load_auto_channels(self):
        """把上次自动分配的虚拟群号恢复进映射表。

        否则重启后 group_to_channel 里只剩 config 写死的两条，
        bot 在新频道里的回复会因为找不到映射而跑去 QQ 侧。
        """
        try:
            rows = self.state.list_synthetic("fatfish_channel")
        except Exception:
            return
        for name, gid in rows:
            self.channel_to_group.setdefault(name, gid)
            self.group_to_channel.setdefault(gid, name)
        if rows:
            self._next_synthetic = max(
                self._next_synthetic, max(gid for _, gid in rows) + 1
            )

    def _build_fatfish_maps(self):
        """从 fatfish 配置重建频道/用户映射（控制台改配置后可以热更新）。"""
        self.channel_to_group = {}
        self.group_to_channel = {}
        for channel, meta in (self.fat_cfg.get("channels") or {}).items():
            if not isinstance(meta, dict):
                continue
            if str(meta.get("type") or "group").lower() == "group":
                try:
                    gid = int(meta.get("group_id") or 0)
                except Exception:
                    gid = 0
                if gid:
                    self.channel_to_group[str(channel)] = gid
                    self.group_to_channel[gid] = str(channel)

        self.user_map = {}
        for name, uid in (self.fat_cfg.get("users") or {}).items():
            try:
                self.user_map[str(name)] = int(uid)
            except Exception:
                pass

        # 这些 user_id 在 FatFish 里显示成「爱丽丝」（与 config.yaml 对应）
        self.alice_ids = set()
        for raw_id in self.fat_cfg.get("alice_user_ids") or [3337894736]:
            try:
                self.alice_ids.add(int(raw_id))
            except Exception:
                pass

    def reload_fatfish(self, fat_cfg: dict):
        self.fat_cfg = fat_cfg or {}
        self.auto_channels = bool(self.fat_cfg.get("auto_channels", True))
        self._build_fatfish_maps()
        self._load_auto_channels()
        log.info("FatFish 映射已重载：channels=%s users=%s",
                 list(self.channel_to_group), list(self.user_map))

    # ---------------- 频道解析 ----------------
    def _self_names(self) -> set[str]:
        names = {str(self.fat_cfg.get("bot_username") or "").strip().casefold()}
        for raw in self.fat_cfg.get("skip_senders") or []:
            names.add(str(raw).strip().casefold())
        return {n for n in names if n}

    def _auto_group_id(self, channel: str) -> int:
        gid = self.channel_to_group.get(channel)
        if gid:
            return gid
        gid = self.state.get_synthetic_user("fatfish_channel", channel)
        if not gid:
            gid = self._next_synthetic
            used = set(self.group_to_channel) | set(self.user_map.values())
            used |= set(self.channel_to_group.values())
            while gid in used:
                gid += 1
            self._next_synthetic = gid + 1
            self.state.set_synthetic_user("fatfish_channel", channel, gid)
            log.info("FatFish 频道 %s 分配虚拟群号 %s", channel, gid)
        self.channel_to_group[channel] = gid
        self.group_to_channel[gid] = channel
        return gid

    def resolve_channel(self, channel: str):
        """把 FatFish 频道解析成 OneBot 会话。

        config 里配了就用配置；没配过的（新建私聊/群）按名字规则自动分配，
        否则用户每开一个新会话都得改配置重启。
        """
        channel = str(channel or "")
        meta = (self.fat_cfg.get("channels") or {}).get(channel)
        if isinstance(meta, dict):
            return meta
        if not self.auto_channels:
            return None

        if channel.startswith("dm:"):
            parts = [p for p in channel.split(":") if p]
            if len(parts) < 3 or parts[-1].casefold() == "system":
                return None
            others = [p for p in parts[1:] if p.casefold() not in self._self_names()]
            if len(others) != 1:
                return None
            uid = self._user_id_for(others[0])
            if not uid:
                return None
            return {"type": "private", "user": uid, "name": others[0]}

        if channel == "general" or channel.startswith(("private:", "group:", "channel:")):
            gid = self._auto_group_id(channel)
            return {"type": "group", "group_id": gid, "name": channel} if gid else None

        return None

    # ---------------- 绑定 ----------------
    def bind_bot(self, bot):
        self.bot = bot

    def bind_snow(self, snow):
        self.snow = snow

    def bind_fatfish(self, fatfish):
        self.fatfish = fatfish

    def bind_dsh(self, dsh):
        self.dsh = dsh

    # ---------------- FatFish -> bot ----------------
    def _user_id_for(self, username: str) -> int:
        name = str(username or "").strip()
        if not name:
            return 0
        if name in self.user_map:
            return self.user_map[name]
        uid = self.state.get_synthetic_user("fatfish", name)
        if not uid:
            while True:
                uid = self._next_synthetic
                self._next_synthetic += 1
                if uid not in self.user_map.values() and uid not in self.group_to_channel:
                    break
            self.state.set_synthetic_user("fatfish", name, uid)
        return uid

    async def on_fatfish(self, msg: Inbound):
        key = f"{msg.channel}:{msg.native_id}"
        if msg.native_id and self.state.seen("fatfish", key):
            return True
        if self.fatfish_brain == "dsh" and self.dsh is not None and self.dsh.connected:
            ok = await self.dsh.send_inbound(
                f"fatfish:{msg.channel}", msg.sender_name or msg.sender, msg.text, msg=msg
            )
            if ok:
                if msg.native_id:
                    self.state.mark_seen("fatfish", key)
                log.info("FatFish -> DSH: #%s %s: %s", msg.channel, msg.sender_name,
                         msg.text[:80].replace("\n", " "))
                return True
        return await self._route_to_bot(msg)

    async def _route_to_bot(self, msg: Inbound) -> bool:
        """把一条 FatFish 消息交给 momoi_bot（原来的链路）。

        DSH 报错/超时/掉线时也会走这里兜底，所以不在这里做去重判断。
        """
        if self.bot is None:
            return False
        msg.user_id = self._user_id_for(msg.sender)
        if not msg.user_id:
            return False
        if msg.is_group:
            gid = msg.group_id or self.channel_to_group.get(msg.channel, 0)
            if not gid:
                log.warning("FatFish 群频道没有映射，跳过: %s", msg.channel)
                return False
            msg.group_id = gid
        else:
            self.private_route[msg.user_id] = (
                "fatfish", msg.channel, time.time() + self.route_window
            )

        event = self._build_event(msg)
        self.state.map_message(
            str(event.get("message_id")), "fatfish", msg.channel, msg.native_id, "in"
        )
        ok = await self.bot.send_event(event)
        if not ok:
            log.warning("bot 未连接，FatFish 消息暂不推进游标: id=%s", msg.native_id)
            return False
        if msg.native_id:
            self.state.mark_seen("fatfish", f"{msg.channel}:{msg.native_id}")
        log.info("FatFish -> bot: #%s %s: %s", msg.channel, msg.sender_name,
                 msg.text[:80].replace("\n", " "))
        return True

    # ---------------- SnowLuma -> bot ----------------
    async def on_snow_event(self, event: dict):
        if event.get("post_type") != "message":
            return
        mid = event.get("message_id")
        key = f"{event.get('self_id')}:{mid}" if mid is not None else ""
        if key and self.state.seen("snowluma", key):
            return
        if self.onebot_brain == "dsh" and self.dsh is not None and self.dsh.connected:
            mt = str(event.get("message_type") or "")
            if mt == "private":
                session = f"onebot:private:{event.get('user_id')}"
            elif mt == "group":
                session = f"onebot:group:{event.get('group_id')}"
            else:
                session = ""
            if session:
                sender = event.get("sender") or {}
                name = str(sender.get("card") or sender.get("nickname") or event.get("user_id") or "")
                text = self._text_of(event.get("message"))
                ok = await self.dsh.send_inbound(session, name, text)
                if ok:
                    if key:
                        self.state.mark_seen("snowluma", key)
                    log.info("SnowLuma -> DSH: %s %s", mt, event.get("user_id"))
                    return
        if self.bot is None:
            return
        if event.get("message_type") == "private":
            try:
                uid = int(event.get("user_id") or 0)
            except Exception:
                uid = 0
            if uid:
                self.private_route[uid] = ("snowluma", str(uid), time.time() + self.route_window)
        ok = await self.bot.send_event(event)
        if ok and key:
            self.state.mark_seen("snowluma", key)
        log.info("SnowLuma -> bot: %s %s", event.get("message_type"), event.get("user_id"))

    # ---------------- DSH -> 平台 ----------------
    async def on_dsh_reply(self, session: str, text: str):
        text = str(text or "").strip()
        if not text:
            return
        try:
            if session.startswith("fatfish:"):
                channel = session.split(":", 1)[1]
                if self.fatfish is not None:
                    await self.fatfish.send_to_channel(channel, text)
                    log.info("DSH -> FatFish: #%s %s", channel, text[:60])
                return
            if session.startswith("onebot:private:"):
                uid = int(session.rsplit(":", 1)[1])
                if self.snow is not None:
                    await self.snow.send_action(
                        "send_private_msg", {"user_id": uid, "message": text}
                    )
                    log.info("DSH -> QQ 私聊: %s %s", uid, text[:60])
                return
            if session.startswith("onebot:group:"):
                gid = int(session.rsplit(":", 1)[1])
                if self.snow is not None:
                    await self.snow.send_action(
                        "send_group_msg", {"group_id": gid, "message": text}
                    )
                    log.info("DSH -> QQ 群: %s %s", gid, text[:60])
                return
            log.warning("未知的 DSH 回复会话: %s", session)
        except Exception:
            log.exception("DSH 回复路由失败: %s", session)

    async def on_dsh_failed(self, session: str, msg=None, reason: str = ""):
        """DSH 没给出回复（报错/超时/掉线）：把原消息改投 momoi_bot，别静默丢。"""
        if msg is not None and session.startswith("fatfish:"):
            log.warning("DSH 未回复（%s），改投 momoi_bot: %s", reason, session)
            if await self._route_to_bot(msg):
                return
            log.error(
                "DSH 和 momoi_bot 都不可用，这条消息丢了: %s %s: %s",
                session, getattr(msg, "sender", ""), str(getattr(msg, "text", ""))[:120],
            )
            return
        log.warning("DSH 未回复（%s）: %s", reason, session)

    # ---------------- bot action -> 平台 ----------------
    async def on_bot_action(self, action: str, params: dict, echo=None):
        try:
            if action == "send_private_msg":
                return await self._send_private(params, echo)
            if action == "send_group_msg":
                return await self._send_group(params, echo)
            if action == "get_msg":
                local = self._local_get_msg(params)
                if local is not None:
                    return self._ok(echo, local)
            if action in ("get_msg", "get_forward_msg", "get_group_info",
                          "get_group_member_info", "get_login_info", "get_status"):
                if self.snow is None:
                    return self._fail(echo, "SnowLuma 未连接")
                return await self.snow.send_action(action, params, echo=echo)
            if self.snow is None:
                return self._fail(echo, f"未实现或 SnowLuma 未连接: {action}")
            return await self.snow.send_action(action, params, echo=echo)
        except Exception as e:
            log.exception("bot action 处理失败: %s", action)
            return self._fail(echo, str(e))

    # ---------------- 本地消息查询 ----------------
    def _local_get_msg(self, params: dict):
        """FatFish 来的消息 Hub 本地就有，不必绕到 QQ 侧去查。

        Hub 给 FatFish 消息编的 id 形如 ff_21，NapCat 那边不认识，转发过去必然失败。
        """
        mid = str((params or {}).get("message_id") or "").strip()
        if not mid.startswith("ff_"):
            return None
        rec = self.state.get_message(mid)
        if rec is None or self.fatfish is None:
            return None
        channel = str(rec["channel"])
        row = self.fatfish.get_message(channel, str(rec["native_id"]))
        if row is None:
            return None
        sender = str(row["sender"] or "")
        uid = (
            self.user_map.get(sender)
            or self.state.get_synthetic_user("fatfish", sender)
            or 0
        )
        name = "爱丽丝" if uid in self.alice_ids else sender
        text = str(row["content"] or "")
        meta = (self.fat_cfg.get("channels") or {}).get(channel) or {}
        is_group = str(meta.get("type") or "group").lower() == "group"
        try:
            group_id = int(meta.get("group_id") or 0)
        except Exception:
            group_id = 0
        data = {
            "message_id": mid,
            "message": [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "time": int(float(row["ts"] or 0)),
            "sender": {"user_id": uid, "nickname": name, "card": name},
        }
        if is_group:
            data.update({"message_type": "group", "group_id": group_id,
                         "sub_type": "normal"})
        else:
            data.update({"message_type": "private", "sub_type": "friend"})
        return data

    async def _send_private(self, params: dict, echo=None):
        try:
            uid = int(params.get("user_id") or 0)
        except Exception:
            uid = 0
        text = self._text_of(params.get("message"))
        route = self.private_route.get(uid)
        if route and route[2] >= time.time() and route[0] == "fatfish" and self.fatfish:
            channel = route[1]
            result = await self.fatfish.send_to_channel(channel, text)
            log.info("bot -> FatFish 私聊: #%s %s", channel, text[:60])
            return self._ok(echo, {"message_id": result or 0})
        if self.snow is None:
            return self._fail(echo, "SnowLuma 未连接")
        return await self.snow.send_action("send_private_msg", params, echo=echo)

    async def _send_group(self, params: dict, echo=None):
        try:
            gid = int(params.get("group_id") or 0)
        except Exception:
            gid = 0
        text = self._text_of(params.get("message"))
        channel = self.group_to_channel.get(gid)
        if channel and self.fatfish:
            result = await self.fatfish.send_to_channel(channel, text)
            log.info("bot -> FatFish 群: #%s %s", channel, text[:60])
            return self._ok(echo, {"message_id": result or 0})
        if self.snow is None:
            return self._fail(echo, "SnowLuma 未连接")
        return await self.snow.send_action("send_group_msg", params, echo=echo)

    # ---------------- OneBot 事件构造 ----------------
    def _build_event(self, msg: Inbound) -> dict:
        name = msg.sender_name or msg.sender
        if msg.user_id in self.alice_ids:
            name = "爱丽丝"
        sender = {
            "user_id": msg.user_id,
            "nickname": name,
            "card": name,
        }
        base = {
            "post_type": "message",
            "self_id": self.bot_qq,
            "time": int(msg.ts or time.time()),
            "message_id": f"ff_{msg.native_id or int(time.time() * 1000)}",
            "user_id": msg.user_id,
            "sender": sender,
        }
        if msg.is_group:
            message = []
            if self.inject_at:
                message.append({"type": "at", "data": {"qq": str(self.bot_qq)}})
            message.append({"type": "text", "data": {"text": msg.text}})
            base.update({
                "message_type": "group",
                "sub_type": "normal",
                "group_id": msg.group_id,
                "message": message,
                "raw_message": msg.text,
            })
        else:
            base.update({
                "message_type": "private",
                "sub_type": "friend",
                "message": msg.text,
                "raw_message": msg.text,
            })
        return base

    @staticmethod
    def _text_of(message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(str((seg.get("data") or {}).get("text") or ""))
            return "".join(parts)
        return str(message or "")

    @staticmethod
    def _ok(echo, data=None):
        return {"status": "ok", "retcode": 0, "data": data or {}, "echo": echo}

    @staticmethod
    def _fail(echo, message: str):
        return {"status": "failed", "retcode": 100, "data": None,
                "message": message, "echo": echo}
