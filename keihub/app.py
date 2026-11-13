import asyncio
import logging
from pathlib import Path

from adapters.bot_ws import BotWSServer
from adapters.dsh_bridge import DshBridge
from adapters.fatfish import FatFishAdapter
from adapters.snowluma_ws import SnowLumaWS
from core.bus import HubBus
from core.config import BASE_DIR, load_config
from core.logging import setup_logging
from core.runtime import HubRuntime
from core.state import HubState
from service.web_console import start_console

log = logging.getLogger("keihub.app")


async def main():
    cfg = load_config()
    hub_cfg = cfg.get("hub", {}) or {}
    setup_logging(str(hub_cfg.get("log_level") or "INFO"))

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

    dsh_cfg = cfg.get("adapters", {}).get("dsh", {}) or {}
    dsh = None
    if dsh_cfg.get("enabled"):
        dsh = DshBridge(
            str(dsh_cfg.get("host") or "127.0.0.1"),
            int(dsh_cfg.get("port") or 3020),
            bus,
            reply_timeout=float(dsh_cfg.get("reply_timeout_seconds") or 180),
            fallback_to_bot=bool(dsh_cfg.get("fallback_to_bot", True)),
        )
        bus.bind_dsh(dsh)
        await dsh.start()
        runtime.dsh = dsh

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
    log.info("  brains  : fatfish=%s onebot=%s dsh=%s",
             bus.fatfish_brain, bus.onebot_brain, bool(dsh))

    try:
        # 常驻：即使两个适配器都暂时停用，也不要让进程退出
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        for task in (snow_task, fat_task):
            task.cancel()
        if dsh is not None:
            await dsh.stop()
        await bot.stop()
        state.close()
        log.info("KeiHub 已退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
