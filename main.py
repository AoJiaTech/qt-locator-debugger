import sys
import asyncio

from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme
from PySide6.QtAsyncio import QAsyncioEventLoop

from app.logger import setup_logger
from app.models.domain import DeviceConfig
from app.storage.repository import SQLAlchemyRepository
from app.ui.main_window import DEFAULT_DEVICES, MainWindow


async def main() -> None:
    # 初始化 DB，加载已保存的设备配置
    repo = SQLAlchemyRepository()
    await repo.init_db()
    saved = await repo.load_devices()
    initial_devices: list[DeviceConfig] = saved if saved else DEFAULT_DEVICES

    # 若是首次启动（DB 为空），将默认设备写入 DB
    if not saved:
        for i, cfg in enumerate(DEFAULT_DEVICES):
            await repo.save_device(cfg, cfg.read_cmd_hex, i)

    window = MainWindow(initial_devices)
    window.show()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    window.destroyed.connect(lambda: future.set_result(None) if not future.done() else None)
    await future


if __name__ == "__main__":
    setup_logger()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setTheme(Theme.AUTO)
    asyncio.run(main(), loop_factory=lambda: QAsyncioEventLoop(app))
