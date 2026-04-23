import json
from abc import ABC, abstractmethod

from alembic import command
from alembic.config import Config
from sqlalchemy import desc, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.domain import Frame, PortConfig, DeviceConfig
from app.models.db import DeviceRecord, ParsedRecord, MeasurementPoint, MeasurementSession


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
            step_port_config = None
            if r.step_port is not None:
                step_port_config = PortConfig(
                    port=r.step_port,
                    baudrate=r.step_baudrate or 9600,
                    bytesize=r.step_bytesize or 8,
                    parity=r.step_parity or "N",
                    stopbits=r.step_stopbits or 1.0,
                )
            configs.append(
                DeviceConfig(
                    device_id=r.device_id,
                    name=r.name,
                    parser_name=r.parser_name,
                    read_cmd_hex=r.read_cmd_hex,
                    port_config=port_config,
                    step_port_config=step_port_config,
                )
            )
        return configs

    async def save_device(
        self,
        cfg: DeviceConfig,
        read_cmd_hex: str,
        sort_order: int,
        port_config: PortConfig | None = None,
        step_port_config: PortConfig | None = None,
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
                existing.step_port = step_port_config.port if step_port_config else None
                existing.step_baudrate = step_port_config.baudrate if step_port_config else None
                existing.step_bytesize = step_port_config.bytesize if step_port_config else None
                existing.step_parity = step_port_config.parity if step_port_config else None
                existing.step_stopbits = step_port_config.stopbits if step_port_config else None
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
                        step_port=step_port_config.port if step_port_config else None,
                        step_baudrate=step_port_config.baudrate if step_port_config else None,
                        step_bytesize=step_port_config.bytesize if step_port_config else None,
                        step_parity=step_port_config.parity if step_port_config else None,
                        step_stopbits=step_port_config.stopbits if step_port_config else None,
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

        async with self._session_factory() as session:
            await session.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id)
                .values(end_time=datetime.now(), cycle_count=cycle_count)
            )
            await session.commit()

    async def pause_session(self, session_id: int, step_index: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id)
                .values(paused_step_index=step_index)
            )
            await session.commit()

    async def resume_session(self, session_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(MeasurementSession).where(MeasurementSession.id == session_id).values(paused_step_index=None)
            )
            await session.commit()

    async def set_session_baseline(self, session_id: int, baseline_mm: float) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id)
                .values(baseline_distance_mm=baseline_mm)
            )
            await session.commit()

    async def get_session(self, session_id: int) -> MeasurementSession | None:
        async with self._session_factory() as session:
            return await session.get(MeasurementSession, session_id)

    async def get_session_points(self, session_id: int) -> list[MeasurementPoint]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MeasurementPoint)
                .where(MeasurementPoint.session_id == session_id)
                .order_by(MeasurementPoint.timestamp)
            )
            return result.scalars().all()

    async def list_sessions(
        self,
        device_id: str | None = None,
        mode: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MeasurementSession]:
        async with self._session_factory() as session:
            stmt = select(MeasurementSession)
            if device_id is not None:
                stmt = stmt.where(MeasurementSession.device_id == device_id)
            if mode is not None:
                stmt = stmt.where(MeasurementSession.mode == mode)
            result = await session.execute(
                stmt.order_by(desc(MeasurementSession.start_time)).limit(limit).offset(offset)
            )
            return result.scalars().all()

    async def add_points(self, points: list[dict]) -> None:
        async with self._session_factory() as session:
            session.add_all([MeasurementPoint(**p) for p in points])
            await session.commit()
