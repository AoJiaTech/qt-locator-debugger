import sys
import asyncio

from PySide6.QtWidgets import QApplication
from PySide6.QtAsyncio import QAsyncioEventLoop
from qfluentwidgets import setTheme, Theme

from app.logger import setup_logger
from app.ui.main_window import MainWindow


async def main() -> None:
    window = MainWindow()
    await window.repository.init_db()
    window.show()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    window.destroyed.connect(lambda: future.set_result(None) if not future.done() else None)
    await future


if __name__ == "__main__":
    setup_logger()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")          # Fluent Widgets 在此基础上叠加自己的样式
    setTheme(Theme.AUTO)            # 跟随系统亮/暗模式
    asyncio.run(main(), loop_factory=lambda: QAsyncioEventLoop(app))
