import json
from abc import ABC, abstractmethod

from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.models.db import DeviceRecord, ParsedRecord, MeasurementSession, MeasurementPoint
from app.models.domain import Frame, PortConfig, DeviceConfig


class BaseRepository(ABC):
    """持久化接口抽象，解耦 Worker 与具体存储后端。"""

    @abstractmethod
    async def save(self, device_id: str, frame: Frame, port: str = "") -> None:
        """将一帧（含解析结果）写入存储。"""
        ...


class SQLAlchemyRepository(BaseRepository):
    """基于 SQLAlchemy async 的 SQLite 持久化实现。"""

    def __init__(self, db_url: str = "sqlite+aiosqlite:///data.db"):
        self._engine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def init_db(self) -> None:
        """运行 Alembic 迁移，确保数据库 schema 是最新的。"""
        import asyncio

        alembic_cfg = Config("alembic.ini")
        await asyncio.get_event_loop().run_in_executor(None, command.upgrade, alembic_cfg, "head")

    # ------------------------------------------------------------------ #
    # 帧存储
    # ------------------------------------------------------------------ #

    async def save(self, device_id: str, frame: Frame, port: str = "") -> None:
        record = ParsedRecord(
            device_id=device_id,
            port=port,
            direction=frame.direction.value,
            raw_hex=frame.raw.hex(" ").upper(),
            parsed_json=json.dumps(frame.parsed, ensure_ascii=False) if frame.parsed else None,
            timestamp=frame.timestamp,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()

    # ------------------------------------------------------------------ #
    # 设备配置 CRUD
    # ------------------------------------------------------------------ #

    async def load_devices(self) -> list[DeviceConfig]:
        """从数据库加载所有设备配置，按 sort_order 排序。"""
        async with self._session_factory() as session:
            result = await session.execute(select(DeviceRecord).order_by(DeviceRecord.sort_order))
            rows = result.scalars().all()
        configs = []
        for r in rows:
            port_config = None
            if r.port is not None:
                port_config = PortConfig(
                    port=r.port,
                    baudrate=r.baudrate or 9600,
                    bytesize=r.bytesize or 8,
                    parity=r.parity or "N",
                    stopbits=r.stopbits or 1.0,
                )
            configs.append(
                DeviceConfig(
                    device_id=r.device_id,
                    name=r.name,
                    parser_name=r.parser_name,
                    read_cmd_hex=r.read_cmd_hex,
                    port_config=port_config,
                )
            )
        return configs

    async def save_device(
        self,
        cfg: DeviceConfig,
        read_cmd_hex: str,
        sort_order: int,
        port_config: PortConfig | None = None,
    ) -> None:
        """新增或更新一条设备配置（upsert）。"""
        async with self._session_factory() as session:
            existing = await session.get(DeviceRecord, cfg.device_id)
            if existing:
                existing.name = cfg.name
                existing.parser_name = cfg.parser_name
                existing.read_cmd_hex = read_cmd_hex
                existing.sort_order = sort_order
                existing.port = port_config.port if port_config else None
                existing.baudrate = port_config.baudrate if port_config else None
                existing.bytesize = port_config.bytesize if port_config else None
                existing.parity = port_config.parity if port_config else None
                existing.stopbits = port_config.stopbits if port_config else None
            else:
                session.add(
                    DeviceRecord(
                        device_id=cfg.device_id,
                        name=cfg.name,
                        parser_name=cfg.parser_name,
                        read_cmd_hex=read_cmd_hex,
                        sort_order=sort_order,
                        port=port_config.port if port_config else None,
                        baudrate=port_config.baudrate if port_config else None,
                        bytesize=port_config.bytesize if port_config else None,
                        parity=port_config.parity if port_config else None,
                        stopbits=port_config.stopbits if port_config else None,
                    )
                )
            await session.commit()

    async def delete_device(self, device_id: str) -> None:
        """删除一条设备配置。"""
        async with self._session_factory() as session:
            await session.execute(delete(DeviceRecord).where(DeviceRecord.device_id == device_id))
            await session.commit()

    async def create_session(
        self,
        device_id: str,
        mode: str,
        step_period_s: float,
        sample_interval_ms: int,
        displacement_peak_mm: float,
    ) -> int:
        from datetime import datetime

        record = MeasurementSession(
            device_id=device_id,
            mode=mode,
            start_time=datetime.now(),
            cycle_count=0,
            step_period_s=step_period_s,
            sample_interval_ms=sample_interval_ms,
            displacement_peak_mm=displacement_peak_mm,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record.id

    async def finish_session(self, session_id: int, cycle_count: int) -> None:
        from datetime import datetime
        from sqlalchemy import update

        async with self._session_factory() as session:
            await session.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id)
                .values(end_time=datetime.now(), cycle_count=cycle_count)
            )
            await session.commit()

    async def add_points(self, points: list[dict]) -> None:
        async with self._session_factory() as session:
            session.add_all([MeasurementPoint(**p) for p in points])
            await session.commit()
