from datetime import datetime

from sqlalchemy import Text, String, DateTime
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


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
