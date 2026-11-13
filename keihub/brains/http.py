"""http 大脑 —— 把消息 POST 给任意一个 HTTP 服务，同步拿回复。

适合两种场景：

1. 别人自己搭的 DSH / 别的模型服务，不想装 ``dsh-hub-bridge`` 插件；
2. 你自己写个小服务做中转，Hub 只当一个渠道适配器。

约定：

    请求  POST <url>
          {"source": "fatfish", "session": "fatfish:general",
           "user": "爱丽丝", "text": "在吗"}
    回复  任何一种都行：
          {"reply": "在呢"}          或        {"text": "在呢"}
          {"message": "在呢"}        或        纯文本 "在呢"

返回空 / 非 2xx / 超时，都算没接管，Bus 会回落到默认脑（momoi_bot）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from .base import Brain
from .registry import register

log = logging.getLogger("keihub.brains.http")

DEFAULTS = {
    "enabled": False,
    "url": "",
    "token": "",
    "timeout_seconds": 60,
}


@register
class HttpBrain(Brain):
    name = "http"
    label = "HTTP 服务（同步返回回复）"

    def __init__(self, cfg: dict, bus):
        merged = {**DEFAULTS, **(cfg or {})}
        super().__init__(merged, bus)
        self.url = str(merged.get("url") or "").strip()
        self.token = str(merged.get("token") or "").strip()
        self.timeout = max(5.0, float(merged.get("timeout_seconds") or 60))

    @property
    def enabled(self) -> bool:
        return bool(super().enabled and self.url)

    @property
    def connected(self) -> bool:
        return bool(self.url)

    # ---------------- 收消息 ----------------
    async def deliver(self, source: str, payload) -> bool:
        if not self.url:
            return False

        if source == "fatfish":
            msg = payload
            session = f"fatfish:{msg.channel}"
            user = msg.sender_name or msg.sender
            text = str(msg.text or "")
        elif source == "onebot":
            event = payload
            mt = str(event.get("message_type") or "")
            if mt == "private":
                session = f"onebot:private:{event.get('user_id')}"
            elif mt == "group":
                session = f"onebot:group:{event.get('group_id')}"
            else:
                return False
            sender = event.get("sender") or {}
            user = str(sender.get("card") or sender.get("nickname")
                       or event.get("user_id") or "")
            text = self.bus._text_of(event.get("message"))
            if not text:
                return False
        else:
            return False

        body = json.dumps(
            {"source": source, "session": session, "user": user, "text": text},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url, data=body, method="POST", headers=headers)

        try:
            raw = await asyncio.to_thread(self._post, req)
        except Exception as e:
            log.warning("http 大脑请求失败（%s）: %s", self.url, e)
            return False

        reply = self._extract(raw)
        if not reply:
            log.warning("http 大脑返回空回复，回落默认脑")
            return False

        log.info("http -> %s: %s", session, reply[:60].replace("\n", " "))
        await self.bus.on_brain_reply(session, reply)
        return True

    def _post(self, req) -> str:
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    @staticmethod
    def _extract(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        try:
            data = json.loads(text)
        except Exception:
            return text
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            for key in ("reply", "text", "message", "content"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            choice = data.get("choices")
            if isinstance(choice, list) and choice:
                msg = (choice[0] or {}).get("message") or {}
                value = msg.get("content")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""
