import asyncio
from typing import Any
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QWidget, QSplitter, QHBoxLayout
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from app.models.domain import DeviceConfig
from app.ui.device_panel import DevicePanel
from app.ui.history_page import HistoryPage
from app.serial.manager import SerialManager
from app.serial.parser import BUILTIN_PARSERS
from app.ui.schedule_page import SchedulePage
from app.schedule.manager import ScheduleManager
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
        schedule_manager: ScheduleManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mainPage")
        self._manager = manager
        self._repository = repository
        self._initial_devices = initial_devices
        self._schedule_manager = schedule_manager
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
        self._device_list.measure_requested.connect(self._on_measure_requested)
        splitter.addWidget(self._device_list)

        self._device_panel = DevicePanel(
            repository=self._repository,
            schedule_manager=self._schedule_manager,
        )
        splitter.addWidget(self._device_panel)

        splitter.setSizes([272, 928])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

    def _ensure_device_tab(self, device_id: str) -> bool:
        worker = self._manager.get_worker(device_id)
        if worker is None:
            self._device_panel.remove_tab(device_id)
            return False

        cfg = self._device_list.get_config(device_id)
        name = cfg.name if cfg else device_id
        read_cmd_hex = cfg.read_cmd_hex if cfg else ""
        self._device_panel.add_tab(
            device_id,
            f"{name}  [{worker.config.port}]",
            worker,
            read_cmd_hex=read_cmd_hex,
        )
        return True

    @Slot(str)
    def _on_device_selected(self, device_id: str) -> None:
        self._ensure_device_tab(device_id)

    @Slot(str)
    def _on_measure_requested(self, device_id: str) -> None:
        if not self._ensure_device_tab(device_id):
            return
        card = self._device_list.get_card(device_id)
        baseline_mm = card._state.baseline if card is not None else None
        self._device_panel.start_measurement(device_id, mode="single", baseline_mm=baseline_mm)

    def resume_session(self, payload: dict[str, Any]) -> None:
        device_id = str(payload["device_id"])
        if not self._ensure_device_tab(device_id):
            return
        self._device_panel.start_from_session(
            device_id=device_id,
            session_id=int(payload["session_id"]),
            step_index=int(payload["step_index"]),
            cycle_count=int(payload["cycle_count"]),
            time_offset=float(payload["time_offset"]),
            history_time=list(payload["history_time"]),
            history_current=list(payload["history_current"]),
            history_distance=list(payload["history_distance"]),
            step_period_s=float(payload["step_period_s"]),
            sample_interval_ms=int(payload["sample_interval_ms"]),
            displacement_peak_mm=float(payload["displacement_peak_mm"]),
            mode=str(payload["mode"]),
            baseline_distance_mm=payload.get("baseline_distance_mm"),
        )


class MainWindow(FluentWindow):
    """主窗口。"""

    def __init__(self, initial_devices: list[DeviceConfig]) -> None:
        super().__init__()
        self.setWindowTitle("串口设备调试工具")
        self.resize(1280, 760)

        self._manager = SerialManager()
        self._repository = SQLAlchemyRepository()
        self._schedule_manager = ScheduleManager(Path("schedule_config.json"))

        self._main_page = _MainPage(
            self._manager,
            self._repository,
            initial_devices,
            schedule_manager=self._schedule_manager,
        )
        self._schedule_page = SchedulePage(self._schedule_manager)
        self._history_page = HistoryPage(self._repository, self._manager)
        self._history_page.resume_requested.connect(self._on_history_resume_requested)
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
        self.addSubInterface(
            self._schedule_page,
            FluentIcon.CALENDAR,
            "定时运行",
            NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self._history_page,
            FluentIcon.HISTORY,
            "历史记录",
            NavigationItemPosition.TOP,
        )
        self.navigationInterface.setExpandWidth(160)
        self.navigationInterface.setCollapsible(False)

    @Slot(int, str)
    def _on_history_resume_requested(self, _session_id: int, _device_id: str) -> None:
        payload = self._history_page.get_selected_resume_payload()
        if payload is None:
            return

        self.switchTo(self._main_page)
        self._main_page.resume_session(payload)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        asyncio.create_task(self._manager.disconnect_all())
        event.accept()
