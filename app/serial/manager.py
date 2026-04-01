from app.logger import logger
from app.models.domain import PortConfig
from app.serial.parser import BaseParser
from app.serial.worker import SerialWorker
from app.storage.repository import BaseRepository


class SerialManager:
    """统一管理所有 SerialWorker 的生命周期，以 device_id 为键。"""

    def __init__(self) -> None:
        self._workers: dict[str, SerialWorker] = {}

    def create_worker(
        self,
        device_id: str,
        config: PortConfig,
        parser: BaseParser | None = None,
        repository: BaseRepository | None = None,
    ) -> SerialWorker:
        if device_id in self._workers:
            logger.warning(f"[Manager] 设备 {device_id} 已存在 Worker，将替换")
        worker = SerialWorker(config, device_id, parser, repository)
        self._workers[device_id] = worker
        return worker

    def get_worker(self, device_id: str) -> SerialWorker | None:
        return self._workers.get(device_id)

    def remove_worker(self, device_id: str) -> None:
        self._workers.pop(device_id, None)

    async def disconnect_all(self) -> None:
        for worker in list(self._workers.values()):
            await worker.disconnect()
        self._workers.clear()
        logger.info("[Manager] 所有串口已断开")
