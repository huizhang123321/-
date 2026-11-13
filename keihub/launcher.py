#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KeiHub 桌面启动器。

双击默认打开 GUI；也可以作为 Hub 子进程/托盘入口：
    KeiHub.exe --run-hub
    KeiHub.exe --tray
    KeiHub.exe --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from core.config import (
    BASE_DIR,
    CONFIG_PATH,
    auto_configure_fatfish,
    discover_fatfish_base,
    load_config,
)
import app as hub_app
import service.tray as tray_module

CONSOLE_URL = "http://127.0.0.1:8765/"
LOG_DIR = BASE_DIR / "logs"
SERVICE_NAME = "KeiHub"


def bootstrap_files():
    """onefile 模式：把内置的配置/服务脚本/DSH 插件释放到 exe 旁边。"""
    if not getattr(sys, "frozen", False):
        return
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return
    src = Path(bundle)
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("config.yaml", "README.md"):
            s = src / name
            d = BASE_DIR / name
            if s.exists() and not d.exists():
                shutil.copy2(s, d)
        for name in ("service", "dsh-plugin"):
            s = src / name
            d = BASE_DIR / name
            if s.is_dir() and not d.exists():
                shutil.copytree(s, d)
    except Exception:
        # 释放失败时仍允许启动器显示错误/让用户手动放配置
        pass


def fetch_status(timeout: float = 1.5) -> dict:
    try:
        with urllib.request.urlopen(CONSOLE_URL + "api/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def service_state() -> str:
    try:
        out = subprocess.run(
            ["sc.exe", "query", SERVICE_NAME],
            capture_output=True, text=True, errors="replace", timeout=5,
        ).stdout or ""
        if "RUNNING" in out:
            return "running"
        if "STOPPED" in out:
            return "stopped"
        if "1060" in out or "FAILED" in out:
            return "missing"
        return "unknown"
    except Exception:
        return "unknown"


def open_path(path: Path):
    try:
        os.startfile(str(path))
    except Exception:
        pass


class Launcher:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.hub_proc_log = None
        root.title("KeiHub")
        root.geometry("720x580")
        root.minsize(680, 540)

        try:
            import tkinter as tk
            from tkinter import messagebox
            self.tk = tk
            self.messagebox = messagebox
        except Exception:
            raise

        bg = "#11151c"
        fg = "#d9e2ef"
        sub = "#8fa3bd"
        root.configure(bg=bg)

        title = tk.Label(root, text="KeiHub", font=("Microsoft YaHei", 20, "bold"),
                         bg=bg, fg=fg)
        title.pack(anchor="w", padx=24, pady=(20, 2))
        tk.Label(root, text="FatFish / SnowLuma / momoi_bot / DSH 中间层",
                 font=("Microsoft YaHei", 10), bg=bg, fg=sub).pack(anchor="w", padx=24)

        setup = tk.Frame(root, bg="#171d27", highlightbackground="#263041",
                         highlightthickness=1)
        setup.pack(fill="x", padx=24, pady=(14, 0))
        tk.Label(setup, text="FatFish API 接入地址", font=("Microsoft YaHei", 9),
                 bg="#171d27", fg=sub, anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        self.api_var = tk.StringVar(value=self._default_api())
        api_entry = tk.Entry(setup, textvariable=self.api_var, font=("Consolas", 9),
                             bg="#0e131a", fg=fg, insertbackground=fg, relief="flat")
        api_entry.pack(fill="x", padx=14, ipady=5)
        tk.Label(setup, text="令牌（可留空，沿用现有配置；也可把 token 放进地址 ?token=...）",
                 font=("Microsoft YaHei", 8), bg="#171d27", fg="#60748c",
                 anchor="w").pack(fill="x", padx=14, pady=(6, 2))
        self.token_var = tk.StringVar()
        token_entry = tk.Entry(setup, textvariable=self.token_var, show="*",
                               font=("Consolas", 9), bg="#0e131a", fg=fg,
                               insertbackground=fg, relief="flat")
        token_entry.pack(fill="x", padx=14, ipady=5)
        row = tk.Frame(setup, bg="#171d27")
        row.pack(fill="x", padx=14, pady=8)
        self.setup_status = tk.StringVar(value="粘贴 FatFish 设置 → API 接入服务 里的地址")
        tk.Label(row, textvariable=self.setup_status, font=("Microsoft YaHei", 8),
                 bg="#171d27", fg="#8fa3bd", anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(row, text="自动配置并测试", command=self.auto_config,
                  font=("Microsoft YaHei", 9), bg="#2563eb", fg="white",
                  activebackground="#1d4ed8", activeforeground="white",
                  relief="flat", padx=12, pady=6).pack(side="right")

        status_frame = tk.Frame(root, bg="#171d27", highlightbackground="#263041",
                                highlightthickness=1)
        status_frame.pack(fill="x", padx=24, pady=16)
        self.bot_var = tk.StringVar(value="momoi_bot：检测中")
        self.snow_var = tk.StringVar(value="SnowLuma：检测中")
        self.dsh_var = tk.StringVar(value="DSH：检测中")
        self.uptime_var = tk.StringVar(value="Hub：未连接")
        for var in (self.bot_var, self.snow_var, self.dsh_var, self.uptime_var):
            tk.Label(status_frame, textvariable=var, font=("Microsoft YaHei", 10),
                     bg="#171d27", fg=fg, anchor="w").pack(fill="x", padx=14, pady=3)

        btn_frame = tk.Frame(root, bg=bg)
        btn_frame.pack(fill="x", padx=24)
        self._button(btn_frame, "启动 Hub", self.start_hub)
        self._button(btn_frame, "停止 Hub", self.stop_hub)
        self._button(btn_frame, "打开控制台", self.open_console)
        self._button(btn_frame, "配置", self.open_config)
        self._button(btn_frame, "日志", self.open_logs)

        btn_frame2 = tk.Frame(root, bg=bg)
        btn_frame2.pack(fill="x", padx=24, pady=(8, 0))
        self._button(btn_frame2, "启动托盘", self.start_tray)
        auto_label = "开机自启" if getattr(sys, "frozen", False) else "安装服务（管理员）"
        self._button(btn_frame2, auto_label, self.install_service)
        self._button(btn_frame2, "DSH 插件目录", self.open_dsh)
        self._button(btn_frame2, "退出启动器", self.root.destroy)

        tk.Label(root, text=f"目录：{BASE_DIR}", font=("Microsoft YaHei", 8),
                 bg=bg, fg="#60748c", anchor="w").pack(fill="x", padx=24, pady=(16, 0))
        tk.Label(root, text="关闭启动器不会停止 Hub；后台由服务/托盘管理。",
                 font=("Microsoft YaHei", 8), bg=bg, fg="#60748c",
                 anchor="w").pack(fill="x", padx=24, pady=(2, 0))

        self.poll()

    def _button(self, parent, text, command):
        btn = self.tk.Button(parent, text=text, command=command,
                             font=("Microsoft YaHei", 9), bg="#243044", fg="#e5edf8",
                             activebackground="#2f3f58", activeforeground="#ffffff",
                             relief="flat", padx=12, pady=6)
        btn.pack(side="left", padx=(0, 6), pady=4)
        return btn

    def _default_api(self):
        try:
            cfg = load_config()
            fat = (cfg.get("adapters", {}) or {}).get("fatfish", {}) or {}
            base = str(fat.get("api_base") or "http://127.0.0.1:5000").rstrip("/")
            server_log = str(fat.get("server_log_path") or "").strip()
            if not server_log:
                db = str(fat.get("db_path") or "")
                if db:
                    server_log = str(Path(db).parent.parent / "server.log")
            # FatFish 端口会漂：显示真正能连上的那个
            base = discover_fatfish_base(base, server_log, timeout=0.4)
            return base + "/api/v1/messages"
        except Exception:
            return "http://127.0.0.1:5000/api/v1/messages"

    def auto_config(self):
        url = self.api_var.get().strip()
        token = self.token_var.get().strip()
        if not url:
            self.setup_status.set("请先填 FatFish API 接入地址")
            return

        info = None
        applied = False
        if fetch_status():
            # Hub 在跑：交给控制台写配置并热更新，避免两边各写一份
            try:
                payload = json.dumps({"url": url, "token": token}).encode("utf-8")
                req = urllib.request.Request(
                    CONSOLE_URL + "api/fatfish/config", data=payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    info = json.loads(resp.read().decode("utf-8", "replace"))
                if not info.get("ok"):
                    raise RuntimeError(info.get("error") or "配置失败")
                applied = bool(info.get("applied"))
            except Exception as e:
                self.setup_status.set(f"配置失败：{e}")
                self.messagebox.showerror("KeiHub", f"自动配置失败：{e}")
                return
        else:
            try:
                info = auto_configure_fatfish(url, token)
            except Exception as e:
                self.setup_status.set(f"配置失败：{e}")
                self.messagebox.showerror("KeiHub", f"自动配置失败：{e}")
                return

        base = info["api_base"]
        effective = str(info.get("effective_api_base") or base)
        health = bool(info.get("health")) if applied else False
        if not applied:
            try:
                with urllib.request.urlopen(effective.rstrip("/") + "/app/health", timeout=3) as resp:
                    health = 200 <= int(resp.status) < 300
            except Exception:
                health = False

        self.api_var.set(base + "/api/v1/messages")
        self.token_var.set("")
        lines = [
            f"API：{base}",
            "令牌：" + ("已写入" if info.get("token_set") else "未改动"),
            "数据库：" + ("已自动找到" if info.get("db_found") else "未找到（远程地址或 FatFish 未安装）"),
            "后端：" + ("在线" if health else "未响应"),
        ]
        if effective != base:
            lines.append(f"端口已漂移，实际：{effective}")
        if not info.get("local"):
            lines.append("远程 API 只能发送，不能读取 FatFish 消息")
        if applied:
            lines.append("Hub 运行中，已热更新，立即生效")
        elif info.get("note"):
            lines.append(str(info["note"]))
        elif fetch_status():
            lines.append("Hub 正在运行，重启 Hub 后生效")
        if not info.get("comments_preserved", True):
            lines.append("注意：本次整份重写了 config.yaml，注释已丢，备份：" + str(info.get("backup")))
        self.setup_status.set(" / ".join(lines))
        self.messagebox.showinfo("KeiHub", "\n".join(lines))

    def poll(self):
        s = fetch_status()
        if s:
            self.bot_var.set("momoi_bot：" + ("已连接" if s.get("bot_connected") else "未连接"))
            self.snow_var.set("SnowLuma：" + ("已连接" if s.get("snow_connected") else "未连接"))
            self.dsh_var.set("DSH：" + ("已连接" if s.get("dsh_connected") else "未连接"))
            self.uptime_var.set("Hub：运行中（%d 分钟）" % max(0, int((s.get("uptime_seconds") or 0) / 60)))
        else:
            state = service_state()
            self.uptime_var.set("Hub：未响应" + ("（服务已停止）" if state == "stopped" else ""))
        self.root.after(2000, self.poll)

    def start_hub(self):
        if fetch_status():
            self.messagebox.showinfo("KeiHub", "Hub 已经在运行。")
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.hub_proc_log = open(LOG_DIR / "launcher_hub.log", "a", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--run-hub"]
        else:
            cmd = [sys.executable, "-u", "app.py"]
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(BASE_DIR), stdout=self.hub_proc_log,
                stderr=subprocess.STDOUT, creationflags=flags,
            )
            self.messagebox.showinfo("KeiHub", "Hub 启动中，稍等几秒看状态。")
        except Exception as e:
            self.messagebox.showerror("KeiHub", f"启动失败：{e}")

    def stop_hub(self):
        state = service_state()
        if state == "running":
            try:
                subprocess.run(["sc.exe", "stop", SERVICE_NAME], timeout=20,
                               capture_output=True, text=True)
                self.messagebox.showinfo("KeiHub", "已请求停止服务。")
            except Exception as e:
                self.messagebox.showerror("KeiHub", f"停止服务失败：{e}")
            return
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
                self.messagebox.showinfo("KeiHub", "已停止 Hub 子进程。")
            except Exception as e:
                self.messagebox.showerror("KeiHub", f"停止失败：{e}")
            return
        self.messagebox.showinfo("KeiHub", "没有找到启动器拉起的 Hub 进程。可在服务里停止。")

    def open_console(self):
        webbrowser.open(CONSOLE_URL)

    def open_config(self):
        open_path(CONFIG_PATH)

    def open_logs(self):
        open_path(LOG_DIR / "hub.log")

    def start_tray(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--tray"]
        else:
            cmd = [sys.executable, str(BASE_DIR / "service" / "tray.py")]
        try:
            subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=flags)
        except Exception as e:
            self.messagebox.showerror("KeiHub", f"托盘启动失败：{e}")

    def install_service(self):
        if getattr(sys, "frozen", False):
            self.install_autostart()
            return
        script = BASE_DIR / "service" / "install_service.ps1"
        if not script.exists():
            self.messagebox.showerror("KeiHub", "找不到 service/install_service.ps1")
            return
        try:
            subprocess.Popen([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command",
                f"Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{script}'",
            ])
            self.messagebox.showinfo("KeiHub", "已请求管理员权限，请在 UAC 弹窗点“是”。")
        except Exception as e:
            self.messagebox.showerror("KeiHub", f"启动安装失败：{e}")

    def open_dsh(self):
        open_path(BASE_DIR / "dsh-plugin" / "dsh-hub-bridge")

    def install_autostart(self):
        """便携版没有 Python 服务脚本：给 exe 建一个登录自启快捷方式。"""
        if not getattr(sys, "frozen", False):
            return
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        startup.mkdir(parents=True, exist_ok=True)
        lnk = startup / "KeiHub.lnk"
        exe = Path(sys.executable)
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
            "$s.TargetPath='%s';$s.Arguments='--run-hub';$s.WorkingDirectory='%s';$s.Save()"
            % (str(lnk).replace("'", "''"), str(exe).replace("'", "''"), str(BASE_DIR).replace("'", "''"))
        )
        try:
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
            self.messagebox.showinfo("KeiHub", f"已创建开机自启：\n{lnk}")
        except Exception as e:
            self.messagebox.showerror("KeiHub", f"创建自启失败：{e}")


def selftest():
    info = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "base_dir": str(BASE_DIR),
        "config": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "app_imported": bool(hub_app),
        "tray_imported": bool(tray_module),
        "time": time.time(),
    }
    try:
        (BASE_DIR / "selftest.txt").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return info


def main():
    bootstrap_files()
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-hub", action="store_true")
    parser.add_argument("--tray", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.run_hub:
        import asyncio
        asyncio.run(hub_app.main())
        return
    if args.tray:
        tray_module.main()
        return
    if args.selftest:
        selftest()
        return

    import tkinter as tk
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
