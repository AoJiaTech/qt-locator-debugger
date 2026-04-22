from app.logger import logger
from app.models.domain import PortConfig
from app.serial.parser import BaseParser
from app.serial.worker import SerialWorker
from app.storage.repository import BaseRepository


class SerialManager:
    """统一管理所有 SerialWorker 的生命周期，支持每设备双串口（query + step）。"""

    def __init__(self) -> None:
        self._workers: dict[tuple[str, str], SerialWorker] = {}

    def create_workers(
        self,
        device_id: str,
        query_config: PortConfig,
        query_parser: BaseParser,
        step_config: PortConfig | None = None,
        step_parser: BaseParser | None = None,
        repository: BaseRepository | None = None,
    ) -> tuple[SerialWorker, SerialWorker]:
        """为指定设备创建查询 worker 和阶跃 worker。
        step_config 为 None 时，阶跃 worker 复用查询串口（单串口兼容）。
        返回 (query_worker, step_worker)。
        """
        # 先清理旧 worker
        self.remove_workers(device_id)

        query_worker = SerialWorker(query_config, device_id, query_parser, repository)
        self._workers[(device_id, "query")] = query_worker

        eff_step_config = step_config or query_config
        eff_step_parser = step_parser or query_parser
        step_worker = SerialWorker(eff_step_config, device_id, eff_step_parser, repository)
        self._workers[(device_id, "step")] = step_worker

        logger.info(f"[Manager] 设备 {device_id}: query={query_config.port}, step={eff_step_config.port}")
        return query_worker, step_worker

    def get_worker(self, device_id: str, role: str) -> SerialWorker | None:
        return self._workers.get((device_id, role))

    def get_device_workers(self, device_id: str) -> tuple[SerialWorker | None, SerialWorker | None]:
        """返回 (query_worker, step_worker)。"""
        return self._workers.get((device_id, "query")), self._workers.get((device_id, "step"))

    def remove_workers(self, device_id: str) -> None:
        self._workers.pop((device_id, "query"), None)
        self._workers.pop((device_id, "step"), None)

    async def disconnect_all(self) -> None:
        for worker in list(self._workers.values()):
            await worker.disconnect()
        self._workers.clear()
        logger.info("[Manager] 所有串口已断开")
