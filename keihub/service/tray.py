"""KeiHub 托盘程序。

托盘只做状态显示和按钮，不直接跑 Hub；Hub 由 Windows 服务拉起。
依赖：pystray、Pillow、pywin32（服务安装）。
"""

import json
import os
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
CONSOLE_URL = "http://127.0.0.1:8765/"
LOG_PATH = BASE_DIR / "logs" / "hub.log"
SERVICE_NAME = "KeiHub"

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("缺少 pystray / Pillow。请先运行：")
    print(r'  D:\AI\python.exe -m pip install pystray Pillow')
    sys.exit(1)


def fetch_status():
    try:
        with urllib.request.urlopen(CONSOLE_URL + "api/status", timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def run_sc(action: str):
    try:
        result = subprocess.run(
            ["sc.exe", action, SERVICE_NAME],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        return result.returncode == 0, (result.stdout or result.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def notify(icon, text: str):
    try:
        icon.notify(text, "KeiHub")
    except Exception:
        pass


def status_item(icon, item):
    s = fetch_status()
    if not s:
        notify(icon, "控制台未响应（服务可能没启动）")
        return
    text = (
        f"bot={'在线' if s.get('bot_connected') else '离线'} "
        f"SnowLuma={'在线' if s.get('snow_connected') else '离线'} "
        f"DSH={'在线' if s.get('dsh_connected') else '离线'}"
    )
    notify(icon, text)


def start_item(icon, item):
    ok, msg = run_sc("start")
    notify(icon, "服务已启动" if ok else f"启动失败: {msg}")


def stop_item(icon, item):
    ok, msg = run_sc("stop")
    notify(icon, "服务已停止" if ok else f"停止失败: {msg}")


def restart_item(icon, item):
    run_sc("stop")
    ok, msg = run_sc("start")
    notify(icon, "服务已重启" if ok else f"重启失败: {msg}")


def console_item(icon, item):
    webbrowser.open(CONSOLE_URL)


def logs_item(icon, item):
    try:
        os.startfile(str(LOG_PATH))
    except Exception as e:
        notify(icon, f"打不开日志: {e}")


def quit_item(icon, item):
    icon.stop()


def make_icon():
    img = Image.new("RGBA", (64, 64), (17, 21, 28, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(37, 99, 235, 255))
    d.text((24, 18), "K", fill=(255, 255, 255, 255))
    return img


def main():
    icon = pystray.Icon(
        "KeiHub",
        make_icon(),
        "KeiHub",
        menu=pystray.Menu(
            pystray.MenuItem("打开控制台", console_item, default=True),
            pystray.MenuItem("服务状态", status_item),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("启动服务", start_item),
            pystray.MenuItem("停止服务", stop_item),
            pystray.MenuItem("重启服务", restart_item),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("查看日志", logs_item),
            pystray.MenuItem("退出托盘", quit_item),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
