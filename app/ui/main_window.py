import asyncio

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QWidget, QSplitter, QHBoxLayout
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from app.models.domain import DeviceConfig
from app.ui.device_panel import DevicePanel
from app.serial.manager import SerialManager
from app.serial.parser import BUILTIN_PARSERS
from app.ui.device_list_panel import DeviceListPanel
from app.storage.repository import SQLAlchemyRepository
from app.serial.parsers.laser import LaserDisplacementParser

# 注册激光位移传感器解析器
BUILTIN_PARSERS["激光位移传感器"] = LaserDisplacementParser

DEFAULT_DEVICES: list[DeviceConfig] = [
    DeviceConfig(device_id="device_1", name="设备 1"),
    DeviceConfig(device_id="device_2", name="设备 2"),
    DeviceConfig(device_id="device_3", name="设备 3"),
]


class _MainPage(QWidget):
    """FluentWindow 的内嵌主页，承载左右分栏布局。"""

    def __init__(
        self,
        manager: SerialManager,
        repository: SQLAlchemyRepository,
        initial_devices: list[DeviceConfig],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainPage")
        self._manager = manager
        self._repository = repository
        self._initial_devices = initial_devices
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(0,0,0,0.08); }")

        self._device_list = DeviceListPanel(
            self._initial_devices,
            self._manager,
            repository=self._repository,  # 帧存储
            db_repo=self._repository,  # 设备配置存储
        )
        self._device_list.setFixedWidth(272)
        self._device_list.device_selected.connect(self._on_device_selected)
        splitter.addWidget(self._device_list)

        self._device_panel = DevicePanel()
        splitter.addWidget(self._device_panel)

        splitter.setSizes([272, 928])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

    @Slot(str)
    def _on_device_selected(self, device_id: str) -> None:
        worker = self._manager.get_worker(device_id)
        cfg = self._device_list.get_config(device_id)
        name = cfg.name if cfg else device_id
        if worker:
            self._device_panel.add_tab(device_id, f"{name}  [{worker.config.port}]", worker)
        else:
            self._device_panel.remove_tab(device_id)


class MainWindow(FluentWindow):
    """主窗口。"""

    def __init__(self, initial_devices: list[DeviceConfig]) -> None:
        super().__init__()
        self.setWindowTitle("串口设备调试工具")
        self.resize(1280, 760)

        self._manager = SerialManager()
        self._repository = SQLAlchemyRepository()

        self._main_page = _MainPage(self._manager, self._repository, initial_devices)
        self._setup_navigation()

    @property
    def repository(self) -> SQLAlchemyRepository:
        return self._repository

    def _setup_navigation(self) -> None:
        self.addSubInterface(
            self._main_page,
            FluentIcon.IOT,
            "调试主页",
            NavigationItemPosition.TOP,
        )
        self.navigationInterface.setExpandWidth(160)
        self.navigationInterface.setCollapsible(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        asyncio.create_task(self._manager.disconnect_all())
        event.accept()
