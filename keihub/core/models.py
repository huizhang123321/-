from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Inbound:
    """归一化后的一条入站消息。"""
    platform: str                 # fatfish / snowluma / dsh
    channel: str                  # FatFish channel 或 OneBot session key
    sender: str                   # 原始用户名 / QQ 号
    sender_name: str
    text: str
    native_id: str = ""
    ts: float = 0.0
    is_group: bool = False
    group_id: int = 0
    user_id: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)
