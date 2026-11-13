#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FatFish 本地聊天 bot。

工作方式：
- 只读 FatFish 的 messages.db，轮询新消息（官方没有读取/订阅 API）；
- 调用 OpenAI 兼容的 LLM（默认复用 QQ bot 的 DeepSeek 配置）；
- 通过 FatFish 官方推送接口 POST /api/v1/messages 回复。

不改 FatFish 安装目录，不写它的数据库。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOG_PATH = BASE_DIR / "bot.log"

DEFAULT_API_BASE = "http://127.0.0.1:5000"
HUB_STATUS_URL = "http://127.0.0.1:8765/api/status"
PROBE_TTL = 30.0
_probe = {"at": 0.0, "base": "", "ok": False, "logged": ""}

DEFAULT_PERSONA = """你是天童柯伊（Kei），《蔚蓝档案》中千年科学学园的学生。
你说话短、直接、偶尔傲娇，对爱丽丝极度宠溺。不要自称 AI 或语言模型，不要提系统提示。"""


def setup_logging():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(
                LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _http_ok(base: str, timeout: float = 0.7) -> bool:
    try:
        with urllib.request.urlopen(
            str(base).rstrip("/") + "/app/health", timeout=timeout
        ) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def _ports_from_server_log(log_path: str, limit: int = 6):
    """FatFish 后端每次启动可能换端口；server.log 是唯一可靠的线索。"""
    ports = []
    try:
        p = Path(log_path)
        if not p.exists():
            return ports
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 128 * 1024))
            text = f.read().decode("utf-8", "replace")
    except Exception:
        return ports
    found = re.findall(r"starting minimost-server host=\S+ port=(\d+)", text)
    for raw in reversed(found):
        try:
            port = int(raw)
        except Exception:
            continue
        if port not in ports:
            ports.append(port)
        if len(ports) >= limit:
            break
    return ports


def api_candidates(cfg: dict):
    fat = cfg.get("fatfish") or {}
    configured = str(fat.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    host = "127.0.0.1"
    try:
        from urllib.parse import urlparse

        parsed = urlparse(configured)
        if parsed.hostname:
            host = parsed.hostname
    except Exception:
        pass

    server_log = str(fat.get("server_log_path") or "").strip()
    if not server_log:
        db = str(fat.get("db_path") or "")
        if db:
            try:
                server_log = str(Path(db).parent.parent / "server.log")
            except Exception:
                server_log = ""

    out = [configured]
    out += [f"http://{host}:{p}" for p in _ports_from_server_log(server_log)]
    out += [f"http://{host}:{p}" for p in range(5000, 5021)]
    out += [f"http://{host}:{p}" for p in range(5100, 5111)]

    seen, ordered = set(), []
    for base in out:
        if base and base not in seen:
            seen.add(base)
            ordered.append(base)
    return ordered


def resolve_api_base(cfg: dict, force: bool = False) -> tuple[str, bool]:
    """返回 (实际可用的 api_base, 是否健康)；结果缓存 30 秒。"""
    now = time.time()
    if not force and _probe["base"] and now - _probe["at"] < PROBE_TTL:
        return _probe["base"], _probe["ok"]

    candidates = api_candidates(cfg)
    configured = candidates[0]
    base, ok = configured, False
    for cand in candidates:
        if _http_ok(cand):
            base, ok = cand, True
            break

    _probe.update(at=now, base=base, ok=ok)
    if ok and base != configured and _probe["logged"] != base:
        _probe["logged"] = base
        logging.info("FatFish 后端端口已漂移：配置=%s，实际=%s", configured, base)
    return base, ok


def hub_running() -> bool:
    """KeiHub 是否在跑：两边都写同一个 messages.db，同时开会重复回复。"""
    try:
        with urllib.request.urlopen(HUB_STATUS_URL, timeout=1.5) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def load_persona(path: str) -> str:
    p = Path(path)
    if not p.exists():
        logging.warning("人设文件不存在，使用内置短人设: %s", path)
        return DEFAULT_PERSONA
    text = p.read_text(encoding="utf-8")
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        persona = str(data.get("persona") or "").strip()
        if persona:
            return persona
    except Exception as e:
        logging.warning("解析人设 YAML 失败，使用内置短人设: %s", e)
    return DEFAULT_PERSONA


def resolve_llm(cfg: dict) -> dict:
    llm = dict(cfg.get("llm") or {})
    if not str(llm.get("api_key") or "").strip():
        fallback = str(llm.get("fallback_config") or "").strip()
        if fallback and Path(fallback).exists():
            try:
                import yaml

                data = yaml.safe_load(Path(fallback).read_text(encoding="utf-8")) or {}
                src = data.get("llm") or {}
                for key in ("api_url", "api_key", "model", "temperature", "max_tokens"):
                    if not llm.get(key) and src.get(key) is not None:
                        llm[key] = src[key]
                logging.info("LLM 配置已从 %s 读取", fallback)
            except Exception as e:
                logging.warning("读取 fallback LLM 配置失败: %s", e)
    llm.setdefault("api_url", "https://api.deepseek.com/v1/chat/completions")
    llm.setdefault("model", "deepseek-v4-pro")
    llm.setdefault("max_tokens", 1024)
    llm.setdefault("temperature", 0.85)
    if not str(llm.get("api_key") or "").strip():
        raise RuntimeError("没有可用的 LLM api_key：请填 config.json 的 llm.api_key，或保留 fallback_config")
    return llm


def db_connect(path: str) -> sqlite3.Connection:
    uri = "file:" + Path(path).as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def latest_message_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()
    return int(row[0] or 0)


def fetch_new(conn: sqlite3.Connection, after_id: int, limit: int):
    return conn.execute(
        "SELECT id, channel, sender, content, content_type, ts, reply_to_id "
        "FROM messages WHERE id > ? AND deleted = 0 ORDER BY id ASC LIMIT ?",
        (int(after_id), int(limit)),
    ).fetchall()


def fetch_context(conn: sqlite3.Connection, channel: str, before_id: int,
                  limit: int, max_chars: int):
    rows = conn.execute(
        "SELECT id, sender, content, content_type FROM messages "
        "WHERE channel = ? AND id < ? AND deleted = 0 AND content_type = 'text' "
        "ORDER BY id DESC LIMIT ?",
        (str(channel), int(before_id), int(limit)),
    ).fetchall()
    rows = list(reversed(rows))
    # 从最旧的开始裁，保证上下文不超过预算
    while rows and sum(len(str(r["content"] or "")) for r in rows) > max_chars:
        rows.pop(0)
    return rows


def build_system_prompt(persona: str, channel: str, alice_usernames=None) -> str:
    alice = [str(x).strip() for x in (alice_usernames or []) if str(x).strip()]
    identity = ""
    if alice:
        identity = (
            "FatFish 用户 " + "、".join(alice) + " 就是爱丽丝本人；"
            "不管对方显示名是什么，都按爱丽丝对待，称呼她「爱丽丝」，用对爱丽丝的温柔和宠溺。"
        )
    return (
        persona.rstrip()
        + "\n\n【FatFish 聊天室】\n"
        + f"你现在在 FatFish 的 #{channel} 频道里聊天。"
        + (("\n" + identity) if identity else "")
        + "像平时一样用短句回复，通常 1-3 句；不要提 AI、模型、系统、记忆、数据库；"
        + "不要复述别人刚说过的话；不要写成长篇总结。"
    )


def context_to_messages(rows, bot_username: str):
    out = []
    bot_key = str(bot_username or "").strip().casefold()
    for row in rows:
        sender = str(row["sender"] or "")
        content = str(row["content"] or "").strip()
        if not content:
            continue
        if bot_key and sender.casefold() == bot_key:
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": "user", "content": f"{sender}: {content}"})
    return out


def call_llm(llm: dict, system_prompt: str, history, user_text: str) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    payload = {
        "model": llm.get("model"),
        "messages": messages,
        "max_tokens": int(llm.get("max_tokens") or 1024),
        "temperature": float(llm.get("temperature") or 0.85),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        str(llm.get("api_url")),
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + str(llm.get("api_key")),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        reasoning_len = len(str(message.get("reasoning_content") or ""))
        logging.warning(
            "LLM 空回复 finish_reason=%s usage=%s reasoning_len=%s",
            finish, usage, reasoning_len,
        )
        raise RuntimeError(f"LLM 空回复（finish_reason={finish}）")
    return content


def clean_reply(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^\s*(?:柯伊|Kei|kei)\s*[:：]\s*", "", text)
    text = re.sub(r"^```(?:\w+)?\s*|\s*```$", "", text).strip()
    if not text or text.upper() == "NO_REPLY":
        return ""
    if len(text) > max_chars:
        cut = text[:max_chars]
        for sep in ("\n", "。", "！", "？", "…"):
            pos = cut.rfind(sep)
            if pos >= max_chars // 2:
                cut = cut[: pos + 1]
                break
        text = cut.strip()
    return text


def strip_mention(text: str, bot_username: str) -> str:
    name = str(bot_username or "").strip()
    if not name:
        return text
    return re.sub(r"@" + re.escape(name) + r"\s*", "", text, flags=re.I).strip()


def fatfish_healthy(cfg: dict, force: bool = False) -> bool:
    base, ok = resolve_api_base(cfg, force=force)
    return bool(base and ok)


def send_fatfish(cfg: dict, channel: str, text: str) -> tuple[int, str]:
    fat = cfg.get("fatfish") or {}
    token = str(fat.get("token") or "").strip()
    if not token:
        raise RuntimeError("未配置 fatfish.token（FatFish 设置 → API 接入服务里生成）")
    base, _ = resolve_api_base(cfg)
    try:
        return _post_message(base, token, channel, text)
    except urllib.error.HTTPError:
        raise
    except Exception as e:
        logging.warning("FatFish API 失败（%s）: %s，重新探测端口后重试", base, e)
        base, _ = resolve_api_base(cfg, force=True)
        return _post_message(base, token, channel, text)


def _post_message(base: str, token: str, channel: str, text: str) -> tuple[int, str]:
    body = json.dumps({"channel": channel, "text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        str(base).rstrip("/") + "/api/v1/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.status), resp.read().decode("utf-8", "replace")[:500]


def send_fatfish_db(cfg: dict, channel: str, text: str) -> tuple[int, str]:
    """无令牌模式：直接写 FatFish 的 messages.db，并推进事件信号。"""
    fat = cfg.get("fatfish") or {}
    db_path = Path(str(fat.get("db_path") or ""))
    if not db_path.exists():
        raise RuntimeError(f"消息库不存在: {db_path}")
    sender = str(fat.get("bot_username") or "kei_bot").strip() or "kei_bot"
    now = time.time()
    client_id = uuid.uuid4().hex

    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
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

    # 让正在打开的 FatFish 界面收到一条事件；表不存在或没权限时不影响消息已写入
    presence_raw = str(fat.get("presence_db_path") or "").strip()
    presence = Path(presence_raw) if presence_raw else db_path.parent.parent / "presence.db"
    if presence.exists():
        try:
            pconn = sqlite3.connect(str(presence), timeout=5)
            pconn.execute("PRAGMA busy_timeout=5000")
            cur = pconn.execute("UPDATE event_signal SET gen = gen + 1 WHERE id = 1")
            if cur.rowcount == 0:
                pconn.execute("INSERT INTO event_signal(id, gen) VALUES(1, 1)")
            pconn.commit()
            pconn.close()
        except Exception as e:
            logging.warning("推进 event_signal 失败（消息已写入）: %s", e)

    return 0, f"db:{mid}"


def send_reply(cfg: dict, channel: str, text: str) -> tuple[int, str]:
    """auto：有 token 走 API，没有 token 直写数据库；也可强制 api/db。"""
    fat = cfg.get("fatfish") or {}
    mode = str(fat.get("send_mode") or "auto").strip().lower()
    token = str(fat.get("token") or "").strip()
    if mode == "db" or (mode != "api" and not token):
        return send_fatfish_db(cfg, channel, text)
    return send_fatfish(cfg, channel, text)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return {"last_id": int(data.get("last_id") or 0)}
        except Exception:
            pass
    return {"last_id": 0}


def save_state(state: dict):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def channel_allowed(channel: str, channels) -> bool:
    wanted = {str(c).strip() for c in (channels or []) if str(c).strip()}
    return "*" in wanted or not wanted or channel in wanted


def process_once(cfg: dict, llm: dict, persona: str, conn: sqlite3.Connection,
                 state: dict, dry_run: bool | None = None) -> int:
    fat = cfg.get("fatfish") or {}
    bot_name = str(fat.get("bot_username") or "").strip()
    bot_key = bot_name.casefold()
    channels = fat.get("channels") or ["general"]
    poll_cfg = cfg.get("behaviour") or {}
    batch = max(1, int(poll_cfg.get("batch_size") or 20))
    min_interval = max(0.0, float(poll_cfg.get("min_reply_interval_seconds") or 1.5))
    max_backlog = max(0, int(poll_cfg.get("max_backlog_seconds") or 900))
    context_limit = max(0, int(poll_cfg.get("context_messages") or 10))
    context_chars = max(500, int(poll_cfg.get("max_context_chars") or 6000))
    max_reply = max(80, int(poll_cfg.get("max_reply_chars") or 1200))
    if dry_run is None:
        dry_run = bool(fat.get("dry_run", True))

    rows = fetch_new(conn, int(state.get("last_id") or 0), batch)
    if not rows:
        return 0

    handled = 0
    now = time.time()
    for row in rows:
        mid = int(row["id"])
        channel = str(row["channel"] or "")
        sender = str(row["sender"] or "")
        content = str(row["content"] or "").strip()
        ctype = str(row["content_type"] or "text")

        # 忽略：自己、系统、非文本、空消息、非目标频道、过旧积压
        skip = (
            not content
            or ctype != "text"
            or (bot_key and sender.casefold() == bot_key)
            or sender.casefold() == "system"
            or not channel_allowed(channel, channels)
            or (max_backlog > 0 and now - float(row["ts"] or 0) > max_backlog)
        )
        if skip:
            state["last_id"] = mid
            continue

        user_text = strip_mention(content, bot_name)
        if not user_text:
            state["last_id"] = mid
            continue

        context_rows = fetch_context(
            conn, channel, mid, context_limit, context_chars
        )
        history = context_to_messages(context_rows, bot_name)
        system_prompt = build_system_prompt(
            persona, channel, (cfg.get("persona") or {}).get("alice_usernames")
        )

        reply = ""
        last_error = None
        for attempt in range(1, 4):
            try:
                reply = call_llm(llm, system_prompt, history, f"{sender}: {user_text}")
                last_error = None
                break
            except Exception as e:
                last_error = e
                logging.warning("LLM 调用失败（%d/3）: %s", attempt, e)
                time.sleep(2 * attempt)
        if not reply:
            logging.error("LLM 连续空回复 message id=%s: %s，使用兜底回复", mid, last_error)
            reply = "唔……刚刚走神了，你再说一遍？"

        reply = clean_reply(reply, max_reply)
        if not reply:
            state["last_id"] = mid
            continue

        if dry_run:
            logging.info("[dry-run] #%s 回复 %s: %s", channel, sender, reply)
        else:
            sent = False
            last_error = None
            for attempt in range(1, 4):
                try:
                    status, body = send_reply(cfg, channel, reply)
                    logging.info("已发送 #%s status=%s %s", channel, status, str(body)[:120])
                    sent = True
                    break
                except urllib.error.HTTPError as e:
                    last_error = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
                    logging.warning("发送失败（%d/3）: %s", attempt, last_error)
                    time.sleep(2 * attempt)
                except Exception as e:
                    last_error = str(e)
                    logging.warning("发送失败（%d/3）: %s", attempt, e)
                    time.sleep(2 * attempt)
            if not sent:
                logging.error("放弃发送 message id=%s: %s", mid, last_error)

        state["last_id"] = mid
        save_state(state)
        handled += 1

        if min_interval:
            time.sleep(min_interval)

    save_state(state)
    return handled


def run_once(cfg: dict, llm: dict, persona: str, dry_run: bool | None = None,
             skip_health: bool = False) -> int:
    if not skip_health and not fatfish_healthy(cfg):
        base, _ = resolve_api_base(cfg)
        logging.warning("FatFish 后端未就绪（%s），本轮跳过", base)
        return 0
    db_path = str((cfg.get("fatfish") or {}).get("db_path") or "")
    conn = db_connect(db_path)
    try:
        state = load_state()
        start_from = str((cfg.get("fatfish") or {}).get("start_from") or "latest").lower()
        max_id = latest_message_id(conn)
        if state["last_id"] <= 0:
            state["last_id"] = max_id if start_from == "latest" else 0
            save_state(state)
            logging.info("初始游标 last_id=%s（start_from=%s）", state["last_id"], start_from)
        elif max_id < state["last_id"]:
            logging.warning("消息库比 state 新（max_id=%s < last_id=%s），重置游标", max_id, state["last_id"])
            state["last_id"] = max_id
            save_state(state)

        return process_once(cfg, llm, persona, conn, state, dry_run=dry_run)
    finally:
        conn.close()


def run_forever(cfg: dict, llm: dict, persona: str, dry_run: bool | None = None):
    fat = cfg.get("fatfish") or {}
    poll = max(0.5, float(fat.get("poll_seconds") or 2.0))
    auto_launch = bool(fat.get("auto_launch", False))
    launch_path = str(fat.get("launch_path") or "").strip()
    launched = False

    logging.info("FatFish bot 启动：频道=%s，bot=%s，send_mode=%s，dry_run=%s",
                 fat.get("channels"), fat.get("bot_username"),
                 fat.get("send_mode", "auto"),
                 fat.get("dry_run", True) if dry_run is None else dry_run)
    mode = str(fat.get("send_mode") or "auto").lower()
    token = str(fat.get("token") or "").strip()
    if mode == "db" or (mode != "api" and not token):
        logging.info("发送方式：直写 messages.db（不需要登录/令牌）")
    else:
        logging.info("发送方式：官方 API（token 已配置）")

    ready = None  # 只在状态翻转时记一条，免得每 2 秒刷屏
    while True:
        try:
            healthy = fatfish_healthy(cfg)
            if healthy != ready:
                if healthy:
                    base, _ = resolve_api_base(cfg)
                    logging.info("FatFish 后端已就绪：%s", base)
                else:
                    logging.warning(
                        "FatFish 后端未就绪（配置 %s），等待它启动…",
                        fat.get("api_base") or DEFAULT_API_BASE,
                    )
                ready = healthy
            if not healthy:
                if auto_launch and not launched and launch_path:
                    logging.info("尝试启动 FatFish 快捷方式: %s", launch_path)
                    try:
                        os.startfile(launch_path)
                        launched = True
                    except Exception as e:
                        logging.warning("启动 FatFish 失败: %s", e)
                time.sleep(poll)
                continue
            run_once(cfg, llm, persona, dry_run=dry_run, skip_health=True)
        except KeyboardInterrupt:
            logging.info("收到 Ctrl+C，退出")
            return
        except sqlite3.Error as e:
            logging.error("读取 messages.db 失败: %s", e)
        except Exception:
            logging.error("主循环异常:\n%s", traceback.format_exc())
        time.sleep(poll)


def main():
    parser = argparse.ArgumentParser(description="FatFish 本地聊天 bot")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只轮询一次后退出")
    parser.add_argument("--dry-run", action="store_true", help="只打印回复，不调用 FatFish API")
    parser.add_argument("--skip-health", action="store_true", help="跳过 FatFish /app/health 检查")
    parser.add_argument("--force", action="store_true", help="即使 KeiHub 在跑也照常启动")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(Path(args.config))

    if not args.once and not args.force and hub_running():
        logging.error("检测到 KeiHub 正在运行（%s）。", HUB_STATUS_URL)
        logging.error("两者会抢同一个 messages.db，导致同一条消息被回复两次。")
        logging.error("请先关掉 KeiHub，或加 --force 强制执行。")
        sys.exit(2)

    llm = resolve_llm(cfg)
    persona = load_persona(str((cfg.get("persona") or {}).get("file") or ""))

    if args.once:
        n = run_once(cfg, llm, persona, dry_run=args.dry_run or None,
                     skip_health=args.skip_health)
        logging.info("本次处理 %d 条", n)
    else:
        run_forever(cfg, llm, persona, dry_run=args.dry_run or None)


if __name__ == "__main__":
    main()
