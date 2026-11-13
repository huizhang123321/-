import sqlite3
import time
from pathlib import Path


class HubState:
    """游标、合成用户 ID、消息映射。"""

    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cursors("
            "platform TEXT NOT NULL, channel TEXT NOT NULL, last_id INTEGER NOT NULL DEFAULT 0,"
            "PRIMARY KEY(platform, channel))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS synthetic_users("
            "platform TEXT NOT NULL, name TEXT NOT NULL, user_id INTEGER NOT NULL,"
            "PRIMARY KEY(platform, name))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS msg_map("
            "hub_id TEXT PRIMARY KEY, platform TEXT NOT NULL, channel TEXT NOT NULL,"
            "native_id TEXT NOT NULL, direction TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS seen("
            "platform TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL,"
            "PRIMARY KEY(platform, key))"
        )
        self.conn.commit()

    def get_cursor(self, platform: str, channel: str) -> int:
        row = self.conn.execute(
            "SELECT last_id FROM cursors WHERE platform=? AND channel=?",
            (platform, channel),
        ).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, platform: str, channel: str, last_id: int):
        self.conn.execute(
            "INSERT INTO cursors(platform, channel, last_id) VALUES(?,?,?) "
            "ON CONFLICT(platform, channel) DO UPDATE SET last_id=excluded.last_id",
            (platform, channel, int(last_id)),
        )
        self.conn.commit()

    def get_synthetic_user(self, platform: str, name: str) -> int:
        row = self.conn.execute(
            "SELECT user_id FROM synthetic_users WHERE platform=? AND name=?",
            (platform, name),
        ).fetchone()
        return int(row[0]) if row else 0

    def set_synthetic_user(self, platform: str, name: str, user_id: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO synthetic_users(platform, name, user_id) VALUES(?,?,?)",
            (platform, name, int(user_id)),
        )
        self.conn.commit()

    def list_synthetic(self, platform: str) -> list:
        rows = self.conn.execute(
            "SELECT name, user_id FROM synthetic_users WHERE platform=?",
            (str(platform),),
        ).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

    def get_message(self, hub_id: str):
        return self.conn.execute(
            "SELECT hub_id, platform, channel, native_id, direction, ts "
            "FROM msg_map WHERE hub_id=?",
            (str(hub_id),),
        ).fetchone()

    def map_message(self, hub_id: str, platform: str, channel: str, native_id: str,
                    direction: str = "in"):
        self.conn.execute(
            "INSERT OR REPLACE INTO msg_map(hub_id, platform, channel, native_id, direction, ts) "
            "VALUES(?,?,?,?,?,?)",
            (str(hub_id), platform, channel, str(native_id), direction, time.time()),
        )
        self.conn.commit()

    def seen(self, platform: str, key: str, ttl: int = 7 * 86400) -> bool:
        row = self.conn.execute(
            "SELECT ts FROM seen WHERE platform=? AND key=?",
            (str(platform), str(key)),
        ).fetchone()
        if not row:
            return False
        ts = float(row[0] or 0)
        if time.time() - ts > ttl:
            self.conn.execute(
                "DELETE FROM seen WHERE platform=? AND key=?", (str(platform), str(key))
            )
            self.conn.commit()
            return False
        return True

    def mark_seen(self, platform: str, key: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO seen(platform, key, ts) VALUES(?,?,?)",
            (str(platform), str(key), time.time()),
        )
        self.conn.commit()

    def prune_seen(self, ttl: int = 7 * 86400) -> int:
        """定期清掉过期的去重记录，否则 seen 表会一直长。"""
        cur = self.conn.execute("DELETE FROM seen WHERE ts < ?", (time.time() - ttl,))
        self.conn.commit()
        return int(cur.rowcount or 0)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
