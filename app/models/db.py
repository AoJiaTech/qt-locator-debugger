from datetime import datetime

from sqlalchemy import Text, Float, String, Integer, DateTime
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


class DeviceRecord(Base):
    """持久化的设备配置，对应左侧列表每个条目。"""

    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    parser_name: Mapped[str] = mapped_column(String(64), default="Raw Hex")
    read_cmd_hex: Mapped[str] = mapped_column(String(128), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 串口配置（可空，未选择时为 NULL）
    port: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    baudrate: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bytesize: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    parity: Mapped[str | None] = mapped_column(String(4), nullable=True, default=None)
    stopbits: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)


class ParsedRecord(Base):
    """每条经过解析的 RX 帧写入此表，供后续查询、绘图使用。"""

    __tablename__ = "parsed_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    port: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(4))  # "TX" / "RX"
    raw_hex: Mapped[str] = mapped_column(Text)  # 原始字节的十六进制字符串
    parsed_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 字符串
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
