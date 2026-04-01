from enum import Enum
from datetime import datetime
from dataclasses import field, dataclass


class Direction(Enum):
    TX = "TX"
    RX = "RX"


@dataclass
class PortConfig:
    port: str
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0


@dataclass
class Frame:
    direction: Direction
    raw: bytes
    timestamp: datetime = field(default_factory=datetime.now)
    parsed: dict | None = None  # None 表示未解析或无解析器


@dataclass
class DeviceConfig:
    device_id: str  # 唯一标识，如 "device_1"
    name: str  # 显示名称，如 "传感器 A"
    port_config: PortConfig | None = None  # 当前绑定的串口；None 表示未配置
    parser_name: str = "Raw Hex"  # 对应 BUILTIN_PARSERS 中的键
