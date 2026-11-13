import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

log = logging.getLogger("keihub.config")

if getattr(sys, "frozen", False):
    # PyInstaller onedir/onefile：配置和数据放在 exe 旁边，而不是临时解包目录
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    env_path = os.environ.get("KEIHUB_CONFIG", "").strip()
    if env_path:
        path = env_path
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def resolve_fatfish_token(cfg: dict) -> str:
    """优先用 config 里的 token；为空时从现有 FatFish bot 配置读取。"""
    fat = cfg.get("adapters", {}).get("fatfish", {}) or {}
    token = str(fat.get("token") or "").strip()
    if token:
        return token
    source = str(fat.get("token_source") or "").strip()
    if source and Path(source).exists():
        try:
            import json
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
            return str((data.get("fatfish") or {}).get("token") or "").strip()
        except Exception:
            pass
    return ""


DEFAULT_FATFISH_BASE = "http://127.0.0.1:5000"
_PROBE_TTL = 30.0
_probe_cache: dict[str, tuple[float, str]] = {}
_probe_logged: dict[str, str] = {}


def http_healthy(base: str, timeout: float = 0.5) -> bool:
    base = str(base or "").rstrip("/")
    if not base:
        return False
    try:
        with urllib.request.urlopen(base + "/app/health", timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def ports_from_server_log(log_path: Path | str, limit: int = 6) -> list[int]:
    """从 FatFish 的 server.log 尾部倒序取出最近的监听端口。

    后端每次启动都可能换端口（5000 被占就挑 5100 之类），日志是唯一可靠的线索。
    """
    ports: list[int] = []
    try:
        p = Path(str(log_path or ""))
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


def fatfish_candidates(configured: str, server_log: Path | str = "") -> list[str]:
    """按优先级列出要探测的 api_base：配置值 → 日志里的端口 → 常用端口段。"""
    host = "127.0.0.1"
    try:
        parsed = urlparse(str(configured or ""))
        if parsed.hostname:
            host = parsed.hostname
    except Exception:
        pass

    out: list[str] = []
    if str(configured or "").strip():
        out.append(str(configured).rstrip("/"))
    for port in ports_from_server_log(server_log):
        out.append(f"http://{host}:{port}")
    for port in list(range(5000, 5021)) + list(range(5100, 5111)):
        out.append(f"http://{host}:{port}")

    seen = set()
    ordered = []
    for base in out:
        if base and base not in seen:
            seen.add(base)
            ordered.append(base)
    return ordered


def discover_fatfish_base(configured: str = "", server_log: Path | str = "",
                          force: bool = False, timeout: float = 0.4,
                          budget: float = 2.0) -> str:
    """返回当下真正可用的 FatFish api_base；探测结果缓存 30 秒。

    后端关着的时候每个候选都要走一次超时，所以整体有 budget 上限，
    免得把调用方（尤其 Web 控制台的请求）拖死。
    """
    configured = str(configured or "").rstrip("/")
    key = configured or DEFAULT_FATFISH_BASE
    now = time.time()

    cached = _probe_cache.get(key)
    if cached and not force and now - cached[0] < _PROBE_TTL:
        return cached[1]

    resolved = configured or DEFAULT_FATFISH_BASE
    deadline = time.time() + max(0.5, float(budget))
    for base in fatfish_candidates(configured or DEFAULT_FATFISH_BASE, server_log):
        if http_healthy(base, timeout=timeout):
            resolved = base
            break
        if time.time() >= deadline:
            break

    _probe_cache[key] = (now, resolved)
    if resolved != configured and _probe_logged.get(key) != resolved:
        _probe_logged[key] = resolved
        log.info("FatFish 后端端口已漂移：配置=%s，实际=%s", configured or "(空)", resolved)
    return resolved


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # JSON 字符串就是合法的 YAML 双引号标量，反斜杠也会被正确转义
    return json.dumps(str(value), ensure_ascii=False)


def _fatfish_block(lines: list[str]):
    """定位 config.yaml 里的 fatfish 块，返回 (起始行, 结束行, 块缩进)。"""
    start, base = None, 0
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if re.match(r"^\s{1,8}fatfish\s*:\s*(?:#.*)?$", body):
            start = i
            base = len(body) - len(body.lstrip())
            break
    if start is None:
        return None, None, 0
    end = len(lines)
    for j in range(start + 1, len(lines)):
        body = lines[j].rstrip("\r\n")
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip())
        if indent <= base and not body.lstrip().startswith("#"):
            end = j
            break
    return start, end, base


def _split_inline_comment(text: str) -> tuple[str, str]:
    """把 `"auto"   # 说明` 拆成 ('"auto"   ', '# 说明')，引号里的 # 不算注释。"""
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return text[:i], text[i:]
        i += 1
    return text, ""


def _patch_fatfish_key(text: str, key: str, value: Any) -> tuple[str, bool]:
    """改 fatfish 块里的一个标量，保留注释和排版（整份 safe_dump 会把注释全丢掉）。"""
    lines = text.splitlines(keepends=True)
    start, end, base = _fatfish_block(lines)
    if start is None:
        return text, False
    pattern = re.compile(rf"^(\s+){re.escape(key)}\s*:(.*)$")
    for k in range(start + 1, end):
        body = lines[k].rstrip("\r\n")
        m = pattern.match(body)
        if m:
            indent, rest = m.group(1), m.group(2)
            head, comment = _split_inline_comment(rest)
            lead = head[: len(head) - len(head.lstrip())] or " "
            gap = head[len(head.rstrip()):]
            new_body = f"{indent}{key}:{lead}{_yaml_scalar(value)}"
            if comment:
                new_body += (gap or " ") + comment
            eol = "\n" if lines[k].endswith("\n") else ""
            lines[k] = new_body + eol
            return "".join(lines), True
    lines.insert(start + 1, f"{' ' * (base + 2)}{key}: {_yaml_scalar(value)}\n")
    return "".join(lines), True


def auto_configure_fatfish(url: str, token: str = "", config_path: Path | str | None = None) -> dict:
    """粘贴 FatFish API 地址，自动写 api_base / 本机数据库路径 / token。

    接受：
      http://127.0.0.1:5000/api/v1/messages
      http://127.0.0.1:5000
      http://127.0.0.1:5000/api/v1/messages?token=xxx
    """
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("API 地址为空")
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("不是有效的 http/https 地址")
    base = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query)
    q_token = ""
    for key in ("token", "access_token", "key"):
        if query.get(key):
            q_token = str(query[key][0]).strip()
            break
    token = str(token or q_token).strip()

    path = Path(config_path or os.environ.get("KEIHUB_CONFIG", "").strip() or CONFIG_PATH)
    if not path.exists():
        raise ValueError(f"配置文件不存在: {path}")
    text = path.read_text(encoding="utf-8")

    changes: dict[str, Any] = {"api_base": base, "enabled": True, "send_mode": "auto"}
    if token:
        changes["token"] = token

    local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
    db_found = False
    if local:
        appdata = Path(os.environ.get("APPDATA", "")) / "FatFish" / "data"
        msg = appdata / "users" / "messages.db"
        pres = appdata / "presence.db"
        if msg.exists():
            changes["db_path"] = str(msg)
            db_found = True
        if pres.exists():
            changes["presence_db_path"] = str(pres)
        srv = appdata / "server.log"
        if srv.exists():
            changes["server_log_path"] = str(srv)

    patched = text
    all_patched = True
    for key, value in changes.items():
        patched, hit = _patch_fatfish_key(patched, key, value)
        all_patched = all_patched and hit

    # 改完必须还能解析，且值确实生效，否则退回整份写回（会丢注释但不至于写坏）
    verified = False
    if all_patched:
        try:
            check = (yaml.safe_load(patched) or {}).get("adapters", {}).get("fatfish", {}) or {}
            verified = all(
                str(check.get(k)) == str(v) for k, v in changes.items() if not isinstance(v, bool)
            ) and bool(check.get("enabled"))
        except Exception:
            verified = False

    backup = path.with_name(
        path.name + f".bak-autoconfig-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}"
    )
    shutil.copy2(path, backup)
    if verified:
        with open(path, "w", encoding="utf-8") as f:
            f.write(patched)
    else:
        cfg = yaml.safe_load(text) or {}
        fat = cfg.setdefault("adapters", {}).setdefault("fatfish", {})
        fat.update(changes)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)

    return {
        "api_base": base,
        "token_set": bool(token),
        "local": local,
        "db_found": db_found,
        "changed": sorted(changes),
        "comments_preserved": bool(verified),
        "backup": str(backup),
        "config": str(path),
    }


def section(cfg: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = cfg
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur
