import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("keihub.snow_http")


def post_action(http_url: str, action: str, params: dict,
                token: str = "", echo=None, timeout: int = 12) -> dict:
    url = str(http_url or "").rstrip("/") or "http://127.0.0.1:3012"
    if not url.endswith("/"):
        url += "/"
    body = json.dumps(
        {"action": action, "params": params or {}, "echo": echo},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")[:300]
        log.warning("SnowLuma HTTP action 失败 %s: HTTP %s %s", action, e.code, text)
        return {"status": "failed", "retcode": e.code, "data": None,
                "message": text, "echo": echo}
    except Exception as e:
        log.warning("SnowLuma HTTP action 失败 %s: %s", action, e)
        return {"status": "failed", "retcode": 100, "data": None,
                "message": str(e), "echo": echo}


def health(http_url: str, timeout: int = 3) -> bool:
    try:
        with urllib.request.urlopen(str(http_url).rstrip("/") + "/", timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False
