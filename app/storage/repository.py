import json
from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.domain import Frame
from app.models.db import Base, ParsedRecord


class BaseRepository(ABC):
    """持久化接口抽象，解耦 Worker 与具体存储后端。"""

    @abstractmethod
    async def save(self, device_id: str, frame: Frame) -> None:
        """将一帧（含解析结果）写入存储。"""
        ...


class SQLAlchemyRepository(BaseRepository):
    """基于 SQLAlchemy async 的 SQLite 持久化实现。"""

    def __init__(self, db_url: str = "sqlite+aiosqlite:///data.db"):
        self._engine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def init_db(self) -> None:
        """创建所有表（首次启动时调用）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def save(self, device_id: str, frame: Frame) -> None:
        record = ParsedRecord(
            device_id=device_id,
            port="",  # 由调用方填入，此处作占位
            direction=frame.direction.value,
            raw_hex=frame.raw.hex(" ").upper(),
            parsed_json=json.dumps(frame.parsed, ensure_ascii=False) if frame.parsed else None,
            timestamp=frame.timestamp,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
