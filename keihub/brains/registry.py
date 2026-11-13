"""大脑注册表。

名字可以带别名，例如 ``bot`` → ``onebot``，这样老配置里的
``fatfish_brain: "bot"`` 不用改也能继续跑。
"""

from __future__ import annotations

import logging

from .base import Brain

log = logging.getLogger("keihub.brains")

_REGISTRY: dict[str, type[Brain]] = {}

# 老配置里的写法映射到新名字
ALIASES = {
    "bot": "onebot",
    "momoi": "onebot",
    "": "onebot",
    "none": "",
}


def register(cls: type[Brain]) -> type[Brain]:
    name = str(getattr(cls, "name", "") or "").strip().lower()
    if not name:
        raise ValueError(f"{cls.__name__} 缺少 name")
    _REGISTRY[name] = cls
    return cls


def resolve(name: str) -> str:
    key = str(name or "").strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    return key


def available() -> list[str]:
    return sorted(_REGISTRY)


def create(name: str, cfg: dict, bus) -> Brain | None:
    key = resolve(name)
    cls = _REGISTRY.get(key)
    if cls is None:
        log.warning("未知的大脑: %s（可选: %s）", name, ", ".join(available()))
        return None
    return cls(cfg, bus)


def describe(brains: dict) -> list[dict]:
    """给控制台用：已实例化的脑子 + 状态。"""
    out = []
    for name in available():
        brain = (brains or {}).get(name)
        if brain is None:
            out.append({"name": name, "label": name, "enabled": False,
                        "connected": False, "loaded": False})
            continue
        row = brain.status()
        row["loaded"] = True
        out.append(row)
    return out
