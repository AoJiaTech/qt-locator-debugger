import asyncio
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QWidget, QSplitter, QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    SpinBox,
    ComboBox,
    BodyLabel,
    TabWidget,
    CardWidget,
    FluentIcon,
    PushButton,
    PlainTextEdit,
    EditableComboBox,
    PrimaryPushButton,
    TransparentPushButton,
)

from app.logger import logger
from app.serial.worker import SerialWorker
from app.models.domain import Frame, Direction
from app.ui.measurement_panel import MeasurementPanel
from app.storage.repository import SQLAlchemyRepository
from app.measurement.controller import MeasurementController

if TYPE_CHECKING:
    from app.schedule.manager import ScheduleManager


class SendPanel(CardWidget):
    """发送控制面板：手动发送 + 循环发送，Fluent 风格。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: SerialWorker | None = None
        self._send_history: list[str] = []
        self.setBorderRadius(8)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ── 输入行 ──────────────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._mode_combo = ComboBox()
        self._mode_combo.addItems(["ASCII", "HEX"])
        self._mode_combo.setFixedWidth(76)
        input_row.addWidget(self._mode_combo)

        self._input_combo = EditableComboBox()
        self._input_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._input_combo.setPlaceholderText("输入发送内容...")
        input_row.addWidget(self._input_combo)

        self._send_btn = PrimaryPushButton(FluentIcon.SEND, "发送")
        self._send_btn.setFixedWidth(88)
        self._send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self._send_btn)
        root.addLayout(input_row)

        # ── 循环发送行 ──────────────────────────────────────────────────
        loop_row = QHBoxLayout()
        loop_row.setSpacing(6)

        loop_row.addWidget(BodyLabel("间隔(ms)"))
        self._interval_spin = SpinBox()
        self._interval_spin.setRange(10, 60000)
        self._interval_spin.setValue(1000)
        self._interval_spin.setFixedWidth(160)
        loop_row.addWidget(self._interval_spin)

        self._loop_btn = PushButton(FluentIcon.PLAY, "循环发送")
        self._loop_btn.clicked.connect(self._on_loop_start)
        loop_row.addWidget(self._loop_btn)

        self._stop_btn = PushButton(FluentIcon.PAUSE, "停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_loop_stop)
        loop_row.addWidget(self._stop_btn)
        loop_row.addStretch()

        # 清空接收区按钮
        self._clear_btn = TransparentPushButton(FluentIcon.DELETE, "清空")
        self._clear_btn.clicked.connect(self._on_clear_request)
        loop_row.addWidget(self._clear_btn)
        root.addLayout(loop_row)

        self._on_clear_cb = None  # 由 PortTab 注入

    def set_worker(self, worker: SerialWorker | None) -> None:
        self._worker = worker
        enabled = worker is not None
        self._send_btn.setEnabled(enabled)
        self._loop_btn.setEnabled(enabled)

    def set_clear_callback(self, cb) -> None:
        self._on_clear_cb = cb

    def _parse_input(self) -> bytes | None:
        text = self._input_combo.currentText().strip()
        if not text:
            return None
        try:
            if self._mode_combo.currentText() == "HEX":
                return bytes.fromhex(text.replace(" ", ""))
            return text.encode()
        except ValueError as e:
            logger.warning(f"输入格式错误: {e}")
            return None

    def _record_history(self, text: str) -> None:
        if text and text not in self._send_history:
            self._send_history.insert(0, text)
            self._send_history = self._send_history[:50]
            self._input_combo.insertItem(0, text)
        logger.info(f"TX 历史: {text}")

    @Slot()
    def _on_send(self) -> None:
        if not self._worker:
            return
        data = self._parse_input()
        if data is None:
            return
        self._record_history(self._input_combo.currentText().strip())
        asyncio.create_task(self._worker.send(data))

    @Slot()
    def _on_loop_start(self) -> None:
        if not self._worker:
            return
        data = self._parse_input()
        if data is None:
            return
        self._record_history(self._input_combo.currentText().strip())
        asyncio.create_task(self._worker.start_loop_send(data, self._interval_spin.value()))
        self._loop_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    @Slot()
    def _on_loop_stop(self) -> None:
        if self._worker:
            asyncio.create_task(self._worker.stop_loop_send())
        self._loop_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    @Slot()
    def _on_clear_request(self) -> None:
        if self._on_clear_cb:
            self._on_clear_cb()


class _PortSubTab(QWidget):
    """Single-port sub-tab: recv area + send panel."""

    _RECV_STYLE = (
        "PlainTextEdit { font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; border-radius: 6px; }"
    )

    def __init__(self, worker: SerialWorker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = worker
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self._recv = PlainTextEdit()
        self._recv.setReadOnly(True)
        self._recv.setPlaceholderText("等待数据...")
        self._recv.setStyleSheet(self._RECV_STYLE)
        layout.addWidget(self._recv, stretch=1)

        self._send_panel = SendPanel()
        self._send_panel.set_worker(worker)
        self._send_panel.set_clear_callback(self._recv.clear)
        layout.addWidget(self._send_panel)

        worker.frame_received.connect(self._on_frame)
        worker.error_occurred.connect(self._on_error)

    def detach(self) -> None:
        try:
            self._worker.frame_received.disconnect(self._on_frame)
            self._worker.error_occurred.disconnect(self._on_error)
        except RuntimeError:
            pass
        self._send_panel.set_worker(None)

    @staticmethod
    def _format_frame(frame: Frame) -> str:
        ts = frame.timestamp.strftime("%H:%M:%S.%f")[:-3]
        direction = "→ TX" if frame.direction == Direction.TX else "← RX"
        hex_str = frame.raw.hex(" ").upper()
        plain = f"[{ts}] {direction} | {hex_str}"
        if frame.parsed:
            plain += f"\n         解析: {frame.parsed}"
        return plain

    @Slot(Frame)
    def _on_frame(self, frame: Frame) -> None:
        self._recv.appendPlainText(self._format_frame(frame))

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._recv.appendPlainText(f"[错误] {msg}")


class PortTab(QWidget):
    """单个设备的收发 Tab 页。双串口时拆分为查询口/阶跃口两个子 Tab。"""

    def __init__(
        self,
        query_worker: SerialWorker,
        step_worker: SerialWorker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dual_port = step_worker is not None and step_worker is not query_worker
        self._sub_tabs: list[_PortSubTab] = []
        self._build_ui(query_worker, step_worker)

    def _build_ui(self, query_worker: SerialWorker, step_worker: SerialWorker | None) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self._dual_port:
            tab_widget = TabWidget()
            query_sub = _PortSubTab(query_worker)
            step_sub = _PortSubTab(step_worker)
            self._sub_tabs = [query_sub, step_sub]
            tab_widget.addTab(query_sub, "查询口")
            tab_widget.addTab(step_sub, "阶跃口")
            layout.addWidget(tab_widget)
        else:
            sub = _PortSubTab(query_worker)
            self._sub_tabs = [sub]
            layout.addWidget(sub)

    def detach_worker(self) -> None:
        for sub in self._sub_tabs:
            sub.detach()


class DevicePanel(QWidget):
    """
    右侧主面板：上半图表区 + 下半多串口 Tab 收发区。
    """

    def __init__(
        self,
        repository: SQLAlchemyRepository | None = None,
        schedule_manager: ScheduleManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tabs: dict[str, PortTab] = {}
        self._chart_panels: dict[str, MeasurementPanel] = {}
        self._controllers: dict[str, MeasurementController] = {}
        self._repository = repository
        self._schedule_manager = schedule_manager
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)

        # 上半：图表 Tab 区（每个连接设备一个 Tab）
        self._chart_tabs = TabWidget()
        self._chart_tabs.setMovable(True)
        splitter.addWidget(self._chart_tabs)

        # 下半：Tab 收发区
        self._tab_widget = TabWidget()
        self._tab_widget.setMovable(True)
        splitter.addWidget(self._tab_widget)

        splitter.setSizes([300, 420])
        layout.addWidget(splitter)

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    def add_tab(
        self,
        device_id: str,
        label: str,
        query_worker: SerialWorker,
        read_cmd_hex: str = "",
        step_worker: SerialWorker | None = None,
    ) -> None:
        if device_id in self._tabs:
            self._tab_widget.setCurrentWidget(self._tabs[device_id])
            panel = self._chart_panels.get(device_id)
            if panel is not None:
                self._chart_tabs.setCurrentWidget(panel)
            return

        tab = PortTab(query_worker, step_worker)
        self._tabs[device_id] = tab
        self._tab_widget.addTab(tab, label, FluentIcon.IOT)
        self._tab_widget.setCurrentWidget(tab)

        panel = MeasurementPanel()
        controller = MeasurementController(
            read_worker=query_worker,
            read_cmd_hex=read_cmd_hex,
            step_worker=step_worker,
            repository=self._repository,
        )
        panel.set_controller(controller)
        if self._schedule_manager is not None:
            panel.set_schedule_manager(self._schedule_manager)
        self._chart_panels[device_id] = panel
        self._controllers[device_id] = controller
        self._chart_tabs.addTab(panel, label, FluentIcon.IOT)
        self._chart_tabs.setCurrentWidget(panel)

    def remove_tab(self, device_id: str) -> None:
        controller = self._controllers.pop(device_id, None)
        if controller is not None:
            controller.stop()
            controller.detach()

        panel = self._chart_panels.pop(device_id, None)
        if panel is not None:
            panel.detach_controller()
            for i in range(self._chart_tabs.count()):
                if self._chart_tabs.widget(i) is panel:
                    self._chart_tabs.removeTab(i)
                    break
            panel.deleteLater()

        tab = self._tabs.pop(device_id, None)
        if tab is None:
            return
        tab.detach_worker()
        for i in range(self._tab_widget.count()):
            if self._tab_widget.widget(i) is tab:
                self._tab_widget.removeTab(i)
                break
        tab.deleteLater()

    def switch_to(self, device_id: str) -> None:
        tab = self._tabs.get(device_id)
        if tab:
            self._tab_widget.setCurrentWidget(tab)
        panel = self._chart_panels.get(device_id)
        if panel:
            self._chart_tabs.setCurrentWidget(panel)

    def start_measurement(self, device_id: str, mode: str = "single", baseline_mm: float | None = None) -> None:
        panel = self._chart_panels.get(device_id)
        if panel is None:
            return
        self._chart_tabs.setCurrentWidget(panel)
        panel.start(mode, baseline_mm=baseline_mm)

    def start_from_session(
        self,
        device_id: str,
        session_id: int,
        step_index: int,
        cycle_count: int,
        time_offset: float,
        history_time: list[float],
        history_current: list[float],
        history_distance: list[float],
        step_period_s: float,
        sample_interval_ms: int,
        displacement_peak_mm: float,
        mode: str = "auto",
        baseline_distance_mm: float | None = None,
    ) -> None:
        panel = self._chart_panels.get(device_id)
        if panel is None:
            return
        self._chart_tabs.setCurrentWidget(panel)
        panel.start_from_session(
            session_id=session_id,
            step_index=step_index,
            cycle_count=cycle_count,
            time_offset=time_offset,
            history_time=history_time,
            history_current=history_current,
            history_distance=history_distance,
            step_period_s=step_period_s,
            sample_interval_ms=sample_interval_ms,
            displacement_peak_mm=displacement_peak_mm,
            mode=mode,
            baseline_distance_mm=baseline_distance_mm,
        )
