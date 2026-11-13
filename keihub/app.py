import asyncio
import logging
from pathlib import Path

from adapters.bot_ws import BotWSServer
from adapters.fatfish import FatFishAdapter
from adapters.snowluma_ws import SnowLumaWS
from brains import create as create_brain
from core.bus import HubBus
from core.config import BASE_DIR, load_config
from core.logging import setup_logging
from core.runtime import HubRuntime
from core.state import HubState
from service.web_console import start_console

log = logging.getLogger("keihub.app")


def _brains_config(cfg: dict) -> dict:
    """整理 brains 段，兼容老配置（只有 adapters.dsh）。"""
    adapters = cfg.get("adapters", {}) or {}
    brains = dict(cfg.get("brains") or {})

    dsh_old = dict(adapters.get("dsh") or {})
    dsh_new = dict(brains.get("dsh") or {})
    brains["dsh"] = {**dsh_old, **dsh_new} if (dsh_old or dsh_new) else {"enabled": False}

    http_new = dict(brains.get("http") or {})
    brains["http"] = http_new or {"enabled": False}

    # onebot 是默认链路，不配置也要有实例，否则控制台会显示成「未启用」
    brains.setdefault("onebot", {})

    brains.pop("active", None)  # 只用于下面的全局选择，不是某个脑的配置
    return brains


def _apply_active_brain(cfg: dict):
    """brains.active 是个便捷写法：routing 没显式指定时，用它当全局默认。"""
    active = str((cfg.get("brains") or {}).get("active") or "").strip()
    if not active:
        return
    routing = cfg.setdefault("routing", {})
    for key in ("fatfish_brain", "onebot_brain"):
        if not str(routing.get(key) or "").strip():
            routing[key] = active


async def main():
    cfg = load_config()
    hub_cfg = cfg.get("hub", {}) or {}
    setup_logging(str(hub_cfg.get("log_level") or "INFO"))
    _apply_active_brain(cfg)

    state_path = Path(str(hub_cfg.get("state_db") or "data/hub.db"))
    if not state_path.is_absolute():
        state_path = BASE_DIR / state_path
    state = HubState(str(state_path))

    bus = HubBus(cfg, state)
    runtime = HubRuntime()
    runtime.loop = asyncio.get_running_loop()
    runtime.bus = bus
    runtime.state = state

    bot_cfg = cfg.get("adapters", {}).get("bot", {}) or {}
    bot = BotWSServer(
        str(bot_cfg.get("host") or "127.0.0.1"),
        int(bot_cfg.get("port") or 3010),
        bus,
    )
    bus.bind_bot(bot)
    await bot.start()
    runtime.bot = bot

    snow_cfg = cfg.get("adapters", {}).get("snowluma", {}) or {}
    snow = SnowLumaWS(snow_cfg, bus)
    # 先绑定：WS 还没连上时 bus 也能走到 HTTP 兜底，而不是直接报「未连接」
    bus.bind_snow(snow)
    snow_task = asyncio.create_task(snow.run(), name="snowluma")
    runtime.snow = snow

    fat_cfg = cfg.get("adapters", {}).get("fatfish", {}) or {}
    fat = FatFishAdapter(cfg, state, bus)
    bus.bind_fatfish(fat)
    fat_task = asyncio.create_task(fat.run(), name="fatfish")
    runtime.fatfish = fat

    # ---- 可选大脑（默认 onebot 走老链路，其余按配置装载）----
    brains: dict = {}
    for name, bcfg in _brains_config(cfg).items():
        brain = create_brain(name, bcfg if isinstance(bcfg, dict) else {}, bus)
        if brain is None:
            continue
        brains[brain.name] = brain
        try:
            await brain.start()
        except Exception:
            log.exception("大脑 %s 启动失败", name)
        runtime.brains[brain.name] = brain
    bus.bind_brains(brains)
    if "dsh" in brains:
        runtime.dsh = getattr(brains["dsh"], "bridge", None)

    if bool(hub_cfg.get("console_enabled", True)):
        start_console(
            runtime,
            str(hub_cfg.get("console_host") or "127.0.0.1"),
            int(hub_cfg.get("console_port") or 8765),
        )

    log.info("KeiHub 已启动")
    log.info("  bot WS : ws://%s:%s", bot_cfg.get("host", "127.0.0.1"), bot_cfg.get("port", 3010))
    log.info("  SnowLuma: %s（HTTP %s）", snow_cfg.get("ws_url"), snow_cfg.get("http_url"))
    log.info("  FatFish : %s", fat_cfg.get("db_path"))
    log.info("  channels: %s", list((fat_cfg.get("channels") or {}).keys()))
    log.info("  brains  : fatfish=%s onebot=%s 已装载=%s",
             bus.fatfish_brain, bus.onebot_brain,
             ", ".join(f"{n}{'' if b.enabled else '(关)'}" for n, b in brains.items()))
    if bus.channel_brains:
        log.info("  按频道指定: %s", bus.channel_brains)

    try:
        # 常驻：即使两个适配器都暂时停用，也不要让进程退出
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        for task in (snow_task, fat_task):
            task.cancel()
        for brain in brains.values():
            try:
                await brain.stop()
            except Exception:
                log.warning("大脑 %s 停止失败", brain.name, exc_info=True)
        await bot.stop()
        state.close()
        log.info("KeiHub 已退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
