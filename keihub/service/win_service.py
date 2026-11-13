"""KeiHub Windows 服务外壳。

服务本身只负责拉起/守护 `app.py` 子进程，不把异步逻辑塞进 SCM。
安装（管理员）：
    D:\\AI\\python.exe E:\\聊天后端\\keihub\\service\\win_service.py install --startup auto
    sc.exe start KeiHub
卸载：
    sc.exe stop KeiHub
    D:\\AI\\python.exe E:\\聊天后端\\keihub\\service\\win_service.py remove
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
SERVICE_LOG = LOG_DIR / "service.log"
CHILD_LOG = LOG_DIR / "service_child.log"

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False


def log(message: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        with open(SERVICE_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


if HAS_PYWIN32:
    class KeiHubService(win32serviceutil.ServiceFramework):
        _svc_name_ = "KeiHub"
        _svc_display_name_ = "KeiHub Chat Hub"
        _svc_description_ = "KeiHub: FatFish / SnowLuma / momoi_bot / DSH 中间层"

        def __init__(self, args):
            super().__init__(args)
            self._stop = threading.Event()
            self.child = None
            self._child_out = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop.set()
            self._terminate_child()

        def SvcDoRun(self):
            servicemanager.LogInfoMsg("KeiHub service starting")
            log("service starting")
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            os.chdir(BASE_DIR)
            while not self._stop.is_set():
                try:
                    self._spawn_child()
                    while not self._stop.is_set() and self.child.poll() is None:
                        self._stop.wait(1)
                    if self._stop.is_set():
                        break
                    log(f"child exited code={self.child.returncode}; restart in 5s")
                    self._stop.wait(5)
                except Exception as e:
                    log(f"service loop error: {e}")
                    self._stop.wait(5)
            self._terminate_child()
            log("service stopped")

        def _spawn_child(self):
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._child_out = open(CHILD_LOG, "a", encoding="utf-8")
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NO_WINDOW
            self.child = subprocess.Popen(
                [sys.executable, "-u", "app.py"],
                cwd=str(BASE_DIR),
                stdout=self._child_out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            log(f"child started pid={self.child.pid}")

        def _terminate_child(self):
            if self.child is None:
                return
            if self.child.poll() is None:
                try:
                    self.child.terminate()
                    self.child.wait(timeout=5)
                except Exception:
                    try:
                        self.child.kill()
                    except Exception:
                        pass
            try:
                if self._child_out:
                    self._child_out.close()
            except Exception:
                pass
            self.child = None
            self._child_out = None


if __name__ == "__main__":
    if not HAS_PYWIN32:
        print("缺少 pywin32。请先运行：")
        print(r'  D:\AI\python.exe -m pip install pywin32')
        sys.exit(1)
    win32serviceutil.HandleCommandLine(KeiHubService)
