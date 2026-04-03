"""测量控制器：驱动阶跃指令发送和距离采样，发出信号给 UI 层。"""

import asyncio
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.logger import logger
from app.models.domain import Direction, Frame
from app.serial.worker import SerialWorker
from app.storage.repository import SQLAlchemyRepository
from app.utils import build_modbus_frame

_STEP_PAYLOADS: list[tuple[float, bytes]] = [
    (0.0, build_modbus_frame(bytes.fromhex("010600000190"))),
    (25.0, build_modbus_frame(bytes.fromhex("010600000320"))),
    (50.0, build_modbus_frame(bytes.fromhex("0106000004B0"))),
    (75.0, build_modbus_frame(bytes.fromhex("010600000640"))),
    (100.0, build_modbus_frame(bytes.fromhex("0106000007D0"))),
    (75.0, build_modbus_frame(bytes.fromhex("010600000640"))),
    (50.0, build_modbus_frame(bytes.fromhex("0106000004B0"))),
    (25.0, build_modbus_frame(bytes.fromhex("010600000320"))),
    (0.0, build_modbus_frame(bytes.fromhex("010600000190"))),
]


class MeasurementController(QObject):
    """驱动阶跃测量流程的控制器。"""

    step_changed = Signal(int, float, int)  # step_index, current_pct, cycle_count
    sample_ready = Signal(float, float, float, float)
    measurement_finished = Signal(int, float)
    measurement_paused = Signal()
    error_occurred = Signal(str)

    _LOCK_MS = 100

    def __init__(
        self,
        worker: SerialWorker,
        read_cmd_hex: str,
        repository: SQLAlchemyRepository | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._read_cmd_hex = read_cmd_hex
        self._repository = repository

        self.step_period_s = 2.0
        self.sample_interval_ms = 200
        self.displacement_peak_mm = 50.0

        self._active = False
        self._paused = False
        self._mode = "single"
        self._current_step = 0
        self._current_pct = 0.0
        self._pending_pct: float | None = None  # 已发出但未收到 echo 的阶跃值
        self._cycle_count = 0
        self._start_time: datetime | None = None
        self._session_id: int | None = None
        self._time_offset: float = 0.0
        self._locked = False
        self._awaiting_distance_response = False
        self._last_send_at: datetime | None = None
        self._point_buffer: list[dict] = []
        self._baseline_distance_mm: float | None = None

        self._step_timer = QTimer(self)
        self._step_timer.timeout.connect(self._on_step_timer)

        self._sample_timer = QTimer(self)
        self._sample_timer.timeout.connect(self._on_sample_timer)

        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.timeout.connect(self._release_lock)

        self._worker.frame_received.connect(self._on_frame_received)

    def start(self, mode: str, baseline_mm: float | None = None) -> None:
        if self._active:
            return
        self._active = True
        self._paused = False
        self._mode = mode
        self._current_step = 0
        self._current_pct = 0.0
        self._pending_pct = None
        self._cycle_count = 0
        self._start_time = datetime.now()
        self._session_id = None
        self._time_offset = 0.0
        self._locked = False
        self._awaiting_distance_response = False
        self._last_send_at = None
        self._point_buffer = []
        self._baseline_distance_mm = baseline_mm  # None 表示用首帧自动归零

        self._send_step()
        self._step_timer.start(int(self.step_period_s * 1000))
        self._sample_timer.start(self.sample_interval_ms)

        if self._repository is not None:
            asyncio.create_task(self._create_db_session())

        logger.info(
            f"[{self._worker.device_id}] 开始测量 mode={mode}, "
            f"step_period={self.step_period_s}s, sample_interval={self.sample_interval_ms}ms"
        )

    def stop(self) -> None:
        if not self._active and not self._paused:
            return
        self._active = False
        self._paused = False
        self._step_timer.stop()
        self._sample_timer.stop()
        self._lock_timer.stop()
        self._awaiting_distance_response = False

        duration_s = self._time_offset
        if self._start_time is not None:
            duration_s += (datetime.now() - self._start_time).total_seconds()
        self.measurement_finished.emit(self._cycle_count, duration_s)

        if self._repository is not None and self._session_id is not None:
            asyncio.create_task(self._pause_then_flush_and_finish())

        logger.info(f"[{self._worker.device_id}] 结束测量 cycles={self._cycle_count}")

    def pause(self) -> None:
        if not self._active:
            return

        self._active = False
        self._paused = True
        self._step_timer.stop()
        self._sample_timer.stop()
        self._lock_timer.stop()
        self._awaiting_distance_response = False

        if self._repository is not None and self._session_id is not None:
            asyncio.create_task(self._flush_points())
            asyncio.create_task(self._repository.pause_session(self._session_id, self._current_step))

        self.measurement_paused.emit()
        logger.info(f"[{self._worker.device_id}] 暂停测量 step={self._current_step}")

    def resume(
        self,
        session_id: int,
        step_index: int,
        time_offset: float,
        mode: str | None = None,
        cycle_count: int | None = None,
        baseline_distance_mm: float | None = None,
    ) -> None:
        if self._active:
            return

        self._active = True
        self._paused = False
        self._session_id = session_id
        self._current_step = step_index
        self._time_offset = time_offset
        if mode is not None:
            self._mode = mode
        if cycle_count is not None:
            self._cycle_count = cycle_count
        self._start_time = datetime.now()
        self._locked = False
        self._awaiting_distance_response = False
        self._pending_pct = None
        self._last_send_at = None
        self._point_buffer = []
        self._baseline_distance_mm = baseline_distance_mm

        if self._repository is not None:
            asyncio.create_task(self._repository.resume_session(session_id))

        self._send_step()
        self._step_timer.start(int(self.step_period_s * 1000))
        self._sample_timer.start(self.sample_interval_ms)

        logger.info(
            f"[{self._worker.device_id}] 恢复测量 session_id={session_id}, "
            f"step={step_index}, time_offset={time_offset}"
        )

    @Slot()
    def _on_step_timer(self) -> None:
        if not self._active:
            return

        # 若距上次发送不足 _LOCK_MS，延迟重试，避免与正在飞行的测距指令过近
        if self._last_send_at is not None:
            elapsed_ms = (datetime.now() - self._last_send_at).total_seconds() * 1000
            remaining_ms = self._LOCK_MS - elapsed_ms
            if remaining_ms > 0:
                QTimer.singleShot(int(remaining_ms) + 1, self._on_step_timer)
                return

        self._current_step += 1
        if self._current_step >= len(_STEP_PAYLOADS):
            self._current_step = 0
            self._cycle_count += 1
            if self._mode == "single":
                self.stop()
                return

        self._send_step()

    @Slot()
    def _on_sample_timer(self) -> None:
        if not self._active or self._locked:
            return

        # 等待距离响应超时保护：超过 3 个采样间隔自动解除
        if self._awaiting_distance_response:
            if self._last_send_at is not None:
                timeout_ms = self.sample_interval_ms * 1.5
                elapsed_ms = (datetime.now() - self._last_send_at).total_seconds() * 1000
                if elapsed_ms < timeout_ms:
                    return
                # 超时，清除标志并重试
                self._awaiting_distance_response = False
            else:
                return

        if self._last_send_at is not None:
            elapsed_ms = (datetime.now() - self._last_send_at).total_seconds() * 1000
            if elapsed_ms < max(self._LOCK_MS, 150):
                return

        hex_text = self._read_cmd_hex.replace(" ", "").replace(":", "")
        if not hex_text:
            return

        try:
            payload = bytes.fromhex(hex_text)
        except ValueError as e:
            msg = f"读取指令格式错误: {e}"
            logger.error(msg)
            self.error_occurred.emit(msg)
            self.stop()
            return

        self._awaiting_distance_response = True
        self._last_send_at = datetime.now()
        asyncio.create_task(self._worker.send(build_modbus_frame(payload)))

    @Slot()
    def _release_lock(self) -> None:
        self._locked = False

    @Slot(Frame)
    def _on_frame_received(self, frame: Frame) -> None:
        if not self._active or frame.direction != Direction.RX:
            return

        parsed = frame.parsed
        if not parsed:
            return

        # 收到阶跃 echo，确认阶跃生效
        if parsed.get("type") == "write_ack":
            if self._pending_pct is not None:
                self._current_pct = self._pending_pct
                self._pending_pct = None
                self.step_changed.emit(self._current_step, self._current_pct, self._cycle_count)
            return

        if parsed.get("type") != "distance":
            return

        self._awaiting_distance_response = False
        distance_mm = float(parsed["distance_mm"])

        # 首帧建立归零基准
        if self._baseline_distance_mm is None:
            self._baseline_distance_mm = distance_mm
            if self._repository is not None and self._session_id is not None:
                asyncio.create_task(
                    self._repository.set_session_baseline(self._session_id, distance_mm)
                )

        relative_mm = distance_mm - self._baseline_distance_mm
        peak = self.displacement_peak_mm
        distance_pct = min(100.0, relative_mm / peak * 100.0) if peak > 0 else 0.0

        elapsed_s = self._time_offset
        if self._start_time is not None:
            elapsed_s = self._time_offset + (frame.timestamp - self._start_time).total_seconds()

        self.sample_ready.emit(elapsed_s, self._current_pct, distance_pct, relative_mm)

        self._point_buffer.append(
            {
                "session_id": self._session_id,
                "timestamp": frame.timestamp,
                "step_index": self._current_step,
                "current_pct": self._current_pct,
                "distance_pct": distance_pct,
                "distance_mm": relative_mm,
                "elapsed_s": elapsed_s,
            }
        )
        if len(self._point_buffer) >= 10 and self._repository is not None and self._session_id is not None:
            asyncio.create_task(self._flush_points())

    def _send_step(self) -> None:
        current_pct, payload = _STEP_PAYLOADS[self._current_step]
        self._pending_pct = current_pct
        self._locked = True
        self._awaiting_distance_response = False
        self._last_send_at = datetime.now()
        self._lock_timer.start(self._LOCK_MS)
        asyncio.create_task(self._worker.send(payload))

    async def _create_db_session(self) -> None:
        if self._repository is None:
            return
        try:
            self._session_id = await self._repository.create_session(
                device_id=self._worker.device_id,
                mode=self._mode,
                step_period_s=self.step_period_s,
                sample_interval_ms=self.sample_interval_ms,
                displacement_peak_mm=self.displacement_peak_mm,
            )
            for point in self._point_buffer:
                point["session_id"] = self._session_id
        except Exception as e:
            logger.error(f"[{self._worker.device_id}] 创建测量 session 失败: {e}")
            self.error_occurred.emit(f"创建测量 session 失败: {e}")

    async def _flush_points(self) -> None:
        if self._repository is None or self._session_id is None:
            return

        points = [point for point in self._point_buffer if point["session_id"] is not None]
        self._point_buffer.clear()
        if not points:
            return

        try:
            await self._repository.add_points(points)
        except Exception as e:
            logger.error(f"[{self._worker.device_id}] 写入测量点失败: {e}")
            self.error_occurred.emit(f"写入测量点失败: {e}")

    async def _pause_then_flush_and_finish(self) -> None:
        if self._repository is None or self._session_id is None:
            return

        try:
            await self._repository.pause_session(self._session_id, self._current_step)
        except Exception as e:
            logger.error(f"[{self._worker.device_id}] 暂停测量 session 失败: {e}")
            self.error_occurred.emit(f"暂停测量 session 失败: {e}")
            return

        await self._flush_and_finish()

    async def _flush_and_finish(self) -> None:
        await self._flush_points()
        if self._repository is None or self._session_id is None:
            return

        try:
            await self._repository.finish_session(self._session_id, self._cycle_count)
        except Exception as e:
            logger.error(f"[{self._worker.device_id}] 更新测量 session 失败: {e}")
            self.error_occurred.emit(f"更新测量 session 失败: {e}")
