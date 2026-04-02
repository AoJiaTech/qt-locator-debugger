import asyncio

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
from app.measurement.controller import MeasurementController
from app.models.domain import Frame, Direction
from app.serial.worker import SerialWorker
from app.storage.repository import SQLAlchemyRepository
from app.ui.measurement_panel import MeasurementPanel


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


class PortTab(QWidget):
    """单个串口的收发 Tab 页。"""

    def __init__(self, worker: SerialWorker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._build_ui()
        worker.frame_received.connect(self._on_frame)
        worker.error_occurred.connect(self._on_error)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 接收区
        self._recv = PlainTextEdit()
        self._recv.setReadOnly(True)
        self._recv.setPlaceholderText("等待数据...")
        self._recv.setStyleSheet(
            "PlainTextEdit { font-family: 'Cascadia Code', 'Consolas', monospace; "
            "font-size: 12px; border-radius: 6px; }"
        )
        layout.addWidget(self._recv, stretch=1)

        # 发送面板
        self._send_panel = SendPanel()
        self._send_panel.set_worker(self._worker)
        self._send_panel.set_clear_callback(self._recv.clear)
        layout.addWidget(self._send_panel)

    def detach_worker(self) -> None:
        try:
            self._worker.frame_received.disconnect(self._on_frame)
            self._worker.error_occurred.disconnect(self._on_error)
        except RuntimeError:
            pass
        self._send_panel.set_worker(None)

    @Slot(Frame)
    def _on_frame(self, frame: Frame) -> None:
        ts = frame.timestamp.strftime("%H:%M:%S.%f")[:-3]
        if frame.direction == Direction.TX:
            prefix = '<span style="color:#0078d4;">→ TX</span>'
        else:
            prefix = '<span style="color:#107c10;">← RX</span>'
        hex_str = frame.raw.hex(" ").upper()
        line = f"[{ts}] {prefix} | {hex_str}"
        if frame.parsed:
            line += f'<br><span style="color:#888;margin-left:32px;">　　 解析: {frame.parsed}</span>'
        # PlainTextEdit 不支持 HTML，改用 appendPlainText 保持性能
        ts_plain = frame.timestamp.strftime("%H:%M:%S.%f")[:-3]
        direction_plain = "→ TX" if frame.direction == Direction.TX else "← RX"
        plain = f"[{ts_plain}] {direction_plain} | {hex_str}"
        if frame.parsed:
            plain += f"\n         解析: {frame.parsed}"
        self._recv.appendPlainText(plain)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._recv.appendPlainText(f"[错误] {msg}")


class DevicePanel(QWidget):
    """
    右侧主面板：上半图表区 + 下半多串口 Tab 收发区。
    """

    def __init__(self, repository: SQLAlchemyRepository | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tabs: dict[str, PortTab] = {}
        self._chart_panels: dict[str, MeasurementPanel] = {}
        self._controllers: dict[str, MeasurementController] = {}
        self._repository = repository
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

    def add_tab(self, device_id: str, label: str, worker: SerialWorker, read_cmd_hex: str = "") -> None:
        if device_id in self._tabs:
            self._tab_widget.setCurrentWidget(self._tabs[device_id])
            panel = self._chart_panels.get(device_id)
            if panel is not None:
                self._chart_tabs.setCurrentWidget(panel)
            return

        tab = PortTab(worker)
        self._tabs[device_id] = tab
        self._tab_widget.addTab(tab, label, FluentIcon.IOT)
        self._tab_widget.setCurrentWidget(tab)

        panel = MeasurementPanel()
        controller = MeasurementController(
            worker=worker,
            read_cmd_hex=read_cmd_hex,
            repository=self._repository,
        )
        panel.set_controller(controller)
        self._chart_panels[device_id] = panel
        self._controllers[device_id] = controller
        self._chart_tabs.addTab(panel, label, FluentIcon.IOT)
        self._chart_tabs.setCurrentWidget(panel)

    def remove_tab(self, device_id: str) -> None:
        controller = self._controllers.pop(device_id, None)
        if controller is not None:
            controller.stop()

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

    def start_measurement(self, device_id: str, mode: str = "single") -> None:
        panel = self._chart_panels.get(device_id)
        if panel is None:
            return
        self._chart_tabs.setCurrentWidget(panel)
        panel.start(mode)
