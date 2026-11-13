"""大脑（brain）抽象层。

KeiHub 收到一条消息后，交给哪套「脑子」回答是可选的：

    onebot  —— momoi_bot（默认，OneBot 11 服务端那条老链路）
    dsh     —— DeepSeek Harness（经 dsh-hub-bridge 插件）
    http    —— 任意 HTTP 服务（同步返回回复，适合接别人的 DSH / 自建服务）

选哪条由 ``routing`` 决定，可以全局选，也能按 FatFish 频道单独选：

    routing:
      fatfish_brain: "onebot"      # 全局默认
      onebot_brain: "onebot"
      channels:
        "dm:huizhang:key": "dsh"   # 这个频道单独交给 DSH

新增一套脑子只要在 ``brains/`` 下加个文件、继承 ``Brain``，不用动 core。
"""

from .base import Brain
from .registry import ALIASES, available, create, describe, register, resolve

# 导入即注册（顺序无所谓）
from . import dsh as _dsh          # noqa: E402,F401
from . import http as _http        # noqa: E402,F401
from . import onebot as _onebot    # noqa: E402,F401

__all__ = [
    "Brain",
    "available",
    "create",
    "describe",
    "register",
    "resolve",
    "ALIASES",
]
