import asyncio

import serial_asyncio
from PySide6.QtCore import Signal, QObject

from app.logger import logger
from app.serial.parser import BaseParser
from app.storage.repository import BaseRepository
from app.models.domain import Frame, Direction, PortConfig


class SerialWorker(QObject):
    """
    管理单个串口的异步读写。每个实例对应一个物理串口。

    信号在 asyncio 协程中 emit，由于使用 QtAsyncio 事件循环桥接，
    Qt 的 Signal/Slot 机制可以直接在同线程中安全调用。
    """

    frame_received = Signal(Frame)  # TX 回显 / RX 数据，统一通过此信号推给 UI
    error_occurred = Signal(str)  # 串口错误描述
    connected = Signal()
    disconnected = Signal()

    def __init__(
        self,
        config: PortConfig,
        device_id: str,
        parser: BaseParser | None = None,
        repository: BaseRepository | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.device_id = device_id
        self.parser = parser
        self._repository = repository

        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._loop_send_task: asyncio.Task | None = None
        self._loop_stop_event = asyncio.Event()
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 连接 / 断开
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        try:
            reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=self.config.bytesize,
                parity=self.config.parity,
                stopbits=self.config.stopbits,
            )
            logger.info(f"[{self.device_id}|{self.config.port}] 串口已连接")
            self.connected.emit()
            self._read_task = asyncio.create_task(self._read_loop(reader))
        except Exception as e:
            msg = f"连接失败: {e}"
            logger.error(f"[{self.device_id}|{self.config.port}] {msg}")
            self.error_occurred.emit(msg)

    async def disconnect(self) -> None:
        if self._loop_send_task:
            await self.stop_loop_send()

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        logger.info(f"[{self.device_id}|{self.config.port}] 串口已断开")
        self.disconnected.emit()

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #

    async def send(self, data: bytes) -> None:
        if self._writer is None:
            self.error_occurred.emit("串口未连接")
            return
        async with self._send_lock:
            self._writer.write(data)
            await self._writer.drain()
            frame = Frame(direction=Direction.TX, raw=data)
            logger.debug(f"[{self.device_id}|{self.config.port}] TX {data.hex(' ').upper()}")
            self.frame_received.emit(frame)

    async def start_loop_send(self, data: bytes, interval_ms: int) -> None:
        """循环发送，直到调用 stop_loop_send() 或收到外部停止事件。"""
        if self._loop_send_task and not self._loop_send_task.done():
            return  # 已在运行，不重复启动
        self._loop_stop_event.clear()
        self._loop_send_task = asyncio.create_task(self._loop_send_worker(data, interval_ms))

    async def stop_loop_send(self) -> None:
        self._loop_stop_event.set()
        if self._loop_send_task:
            try:
                await self._loop_send_task
            except asyncio.CancelledError:
                pass
            self._loop_send_task = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                parsed = self.parser.parse(chunk) if self.parser else None
                frame = Frame(direction=Direction.RX, raw=chunk, parsed=parsed)
                logger.debug(f"[{self.device_id}|{self.config.port}] RX {chunk.hex(' ').upper()}")
                self.frame_received.emit(frame)
                if parsed and self._repository:
                    asyncio.create_task(self._repository.save(self.device_id, frame, self.config.port))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            msg = f"读取错误: {e}"
            logger.error(f"[{self.device_id}|{self.config.port}] {msg}")
            self.error_occurred.emit(msg)
        finally:
            self.disconnected.emit()

    async def _loop_send_worker(self, data: bytes, interval_ms: int) -> None:
        interval_s = interval_ms / 1000.0
        logger.info(f"[{self.device_id}|{self.config.port}] 开始循环发送，间隔 {interval_ms}ms")
        try:
            while not self._loop_stop_event.is_set():
                await self.send(data)
                try:
                    await asyncio.wait_for(self._loop_stop_event.wait(), timeout=interval_s)
                except TimeoutError:
                    pass  # 正常超时，继续下一轮
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"[{self.device_id}|{self.config.port}] 循环发送已停止")
