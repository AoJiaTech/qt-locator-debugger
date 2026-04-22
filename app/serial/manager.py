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

        step_config 为 None 时，阶跃 worker 直接复用 query worker 实例（单串口兼容），
        manager 字典中 query/step 两个 key 指向同一对象，避免下游误判为双串口。
        返回 (query_worker, step_worker)。

        注意：此方法只负责创建 + 登记新 worker，**不会**主动断开旧 worker。
        旧 worker 上的 Qt 信号（如 disconnected）若被异步触发，会回调到 UI 槽里
        把刚装上的新 worker 又清掉，造成竞态。调用方需自行保证替换前已断开 + detach。
        """
        if device_id in {k[0] for k in self._workers}:
            logger.warning(
                f"[Manager] 设备 {device_id} 已存在 worker，调用方应先 disconnect 再 create_workers"
            )

        self.remove_workers(device_id)

        query_worker = SerialWorker(query_config, device_id, query_parser, repository)
        self._workers[(device_id, "query")] = query_worker

        if step_config is None:
            # 单串口模式：阶跃 worker 与查询 worker 共用同一实例
            step_worker = query_worker
            self._workers[(device_id, "step")] = query_worker
            logger.info(f"[Manager] 设备 {device_id}: query=step={query_config.port} (单串口模式)")
        else:
            eff_step_parser = step_parser or query_parser
            step_worker = SerialWorker(step_config, device_id, eff_step_parser, repository)
            self._workers[(device_id, "step")] = step_worker
            logger.info(f"[Manager] 设备 {device_id}: query={query_config.port}, step={step_config.port}")

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
        # 用 set 去重：单串口模式下 query/step 指向同一 worker，避免重复断开
        for worker in set(self._workers.values()):
            await worker.disconnect()
        self._workers.clear()
        logger.info("[Manager] 所有串口已断开")
