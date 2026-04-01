import asyncio

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSplitter, QWidget
from qfluentwidgets import FluentWindow, FluentIcon, NavigationItemPosition, setTheme, Theme

from app.models.domain import DeviceConfig
from app.serial.manager import SerialManager
from app.storage.repository import SQLAlchemyRepository
from app.ui.device_list_panel import DeviceListPanel
from app.ui.device_panel import DevicePanel

DEFAULT_DEVICES: list[DeviceConfig] = [
    DeviceConfig(device_id="device_1", name="设备 1"),
    DeviceConfig(device_id="device_2", name="激光位移传感器"),
    DeviceConfig(device_id="device_3", name="设备 3"),
]


class _MainPage(QWidget):
    """FluentWindow 的内嵌主页，承载左右分栏布局。"""

    def __init__(
        self,
        manager: SerialManager,
        repository: SQLAlchemyRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainPage")
        self._manager = manager
        self._repository = repository
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(0,0,0,0.08); }")

        # 左侧设备列表
        self._device_list = DeviceListPanel(DEFAULT_DEVICES, self._manager, self._repository)
        self._device_list.setFixedWidth(272)
        self._device_list.device_selected.connect(self._on_device_selected)
        splitter.addWidget(self._device_list)

        # 右侧收发面板
        self._device_panel = DevicePanel()
        splitter.addWidget(self._device_panel)

        splitter.setSizes([272, 928])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

    @Slot(str)
    def _on_device_selected(self, device_id: str) -> None:
        worker = self._manager.get_worker(device_id)
        cfg = next((d for d in DEFAULT_DEVICES if d.device_id == device_id), None)
        name = cfg.name if cfg else device_id
        if worker:
            self._device_panel.add_tab(device_id, f"{name}  [{worker.config.port}]", worker)
        else:
            self._device_panel.remove_tab(device_id)


class MainWindow(FluentWindow):
    """
    主窗口，基于 FluentWindow（无边框 + Mica/Acrylic 背景）。
    导航栏最小化，仅保留主页入口；主体为左右分栏调试界面。
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("串口设备调试工具")
        self.resize(1280, 760)

        self._manager = SerialManager()
        self._repository = SQLAlchemyRepository()

        self._main_page = _MainPage(self._manager, self._repository)
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
