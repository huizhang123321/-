import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import BASE_DIR

MAX_BYTES = 8 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging(level: str = "INFO"):
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            # 轮转：hub.log 超过 8MB 就换 hub.log.1，最多留 5 个
            RotatingFileHandler(
                log_dir / "hub.log",
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )
