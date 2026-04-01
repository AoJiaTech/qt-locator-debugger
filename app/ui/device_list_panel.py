"""设备列表面板与通用设备卡片。"""

import uuid
import asyncio

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import QFrame, QWidget, QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    InfoBar,
    ComboBox,
    LineEdit,
    BodyLabel,
    CardWidget,
    FluentIcon,
    PushButton,
    ScrollArea,
    ToolButton,
    CaptionLabel,
    SwitchButton,
    SubtitleLabel,
    InfoBarPosition,
    LargeTitleLabel,
    StrongBodyLabel,
)

from app.utils import build_modbus_frame
from app.serial.manager import SerialManager
from app.serial.parser import BUILTIN_PARSERS
from app.storage.repository import BaseRepository
from app.models.domain import PortConfig, DeviceConfig, MeasurementState

BAUDRATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

_COLOR_CONNECTED = "#0078d4"
_COLOR_DISCONNECTED = "#8a8a8a"


class StatusDot(QWidget):
    """小圆点状态指示器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._connected = False

    def set_connected(self, value: bool) -> None:
        self._connected = value
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        from PySide6.QtGui import QPen, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(_COLOR_CONNECTED if self._connected else _COLOR_DISCONNECTED)
        painter.setBrush(color)
        painter.setPen(QPen(color.darker(120), 1))
        painter.drawEllipse(1, 1, 8, 8)


class _MeasurementDisplay(CardWidget):
    """卡片内嵌的测量值展示区。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setBorderRadius(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self._hint = CaptionLabel("相对距离")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)

        self._value = LargeTitleLabel("—")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._value)

        self._unit = CaptionLabel("mm")
        self._unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._unit)

        self._baseline_info = CaptionLabel("")
        self._baseline_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._baseline_info)

    def set_relative(self, value: float, unit: str) -> None:
        self._hint.setText("相对距离")
        self._value.setText(f"{value:.3f}")
        self._unit.setText(unit)
        self._baseline_info.setText("")

    def set_absolute(self, absolute: float, baseline: float, unit: str) -> None:
        self._hint.setText("绝对距离 = 基准值 - 相对距离")
        self._value.setText(f"{absolute:.3f}")
        self._unit.setText(unit)
        self._baseline_info.setText(f"基准值: {baseline:.3f} {unit}")

    def reset(self) -> None:
        self._hint.setText("相对距离")
        self._value.setText("—")
        self._baseline_info.setText("")


class DeviceCard(CardWidget):
    """
    通用设备卡片。

    包含串口配置、连接控制，以及通用测量功能：
      - 用户在卡片内填写读取命令（十六进制，不含CRC）
      - 归零：发送一次读取，将响应值记为基准值
      - 读取：发送读取命令，显示相对距离或绝对距离
    解析器输出 {"type": "distance", "distance_mm": float} 时触发测量显示。
    """

    device_selected = Signal(str)  # device_id
    remove_requested = Signal(str)  # device_id

    def __init__(
        self,
        device_config: DeviceConfig,
        manager: SerialManager,
        repository: BaseRepository | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = device_config
        self._manager = manager
        self._repository = repository
        self.worker = None
        self._is_selected = False
        self._state = MeasurementState()

        self.setBorderRadius(8)
        self._build_ui()
        self._refresh_ports()

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── 标题行 ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._dot = StatusDot()
        header.addWidget(self._dot)
        header.addSpacing(6)

        self._title_label = StrongBodyLabel(self._config.name)
        header.addWidget(self._title_label)
        header.addStretch()

        self._refresh_btn = ToolButton(FluentIcon.SYNC)
        self._refresh_btn.setFixedSize(28, 28)
        self._refresh_btn.setToolTip("刷新串口列表")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        header.addWidget(self._refresh_btn)

        self._remove_btn = ToolButton(FluentIcon.DELETE)
        self._remove_btn.setFixedSize(28, 28)
        self._remove_btn.setToolTip("移除此设备")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._config.device_id))
        header.addWidget(self._remove_btn)
        root.addLayout(header)

        # ── 串口 / 波特率 / 解析器 ──────────────────────────────────────
        form = QVBoxLayout()
        form.setSpacing(6)
        form.addLayout(self._row("串口", self._make_port_combo()))
        form.addLayout(self._row("波特率", self._make_baud_combo()))
        form.addLayout(self._row("解析", self._make_parser_combo()))
        root.addLayout(form)

        # ── 连接开关 ────────────────────────────────────────────────────
        conn_row = QHBoxLayout()
        self._switch = SwitchButton("已断开")
        self._switch.setChecked(False)
        self._switch.checkedChanged.connect(self._on_switch_changed)
        conn_row.addWidget(self._switch)
        conn_row.addStretch()
        root.addLayout(conn_row)

        # ── 分隔线 ───────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(0,0,0,0.08);")
        root.addWidget(sep)

        # ── 读取命令输入 ──────────────────────────────────────────────────
        self._cmd_edit = LineEdit()
        self._cmd_edit.setPlaceholderText("读取指令（HEX，不含CRC），如 01 03 00 0D 00 04")
        self._cmd_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self._cmd_edit)

        # ── 测量显示区 ────────────────────────────────────────────────────
        self._display = _MeasurementDisplay()
        root.addWidget(self._display)

        # ── 归零 / 读取距离 ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._zero_btn = PushButton(FluentIcon.SYNC, "归零")
        self._zero_btn.clicked.connect(self._on_zero)
        btn_row.addWidget(self._zero_btn)

        self._read_btn = PushButton(FluentIcon.SEARCH, "读取距离")
        self._read_btn.clicked.connect(self._on_read)
        btn_row.addWidget(self._read_btn)
        root.addLayout(btn_row)

        self._set_measurement_enabled(False)

    @staticmethod
    def _row(label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = BodyLabel(label_text)
        lbl.setFixedWidth(42)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        row.addSpacing(6)
        row.addWidget(widget)
        return row

    def _make_port_combo(self) -> QWidget:
        self._port_combo = ComboBox()
        self._port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._port_combo

    def _make_baud_combo(self) -> QWidget:
        self._baud_combo = ComboBox()
        self._baud_combo.addItems(BAUDRATES)
        self._baud_combo.setCurrentText(str(self._config.port_config.baudrate if self._config.port_config else 115200))
        self._baud_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._baud_combo

    def _make_parser_combo(self) -> QWidget:
        self._parser_combo = ComboBox()
        self._parser_combo.addItems(list(BUILTIN_PARSERS.keys()))
        self._parser_combo.setCurrentText(self._config.parser_name)
        self._parser_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._parser_combo

    def _set_measurement_enabled(self, enabled: bool) -> None:
        self._zero_btn.setEnabled(enabled)
        self._read_btn.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # 选中高亮
    # ------------------------------------------------------------------ #

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        if selected:
            self.setStyleSheet(
                "DeviceCard { background-color: rgba(0, 120, 212, 0.10); "
                "border: 1.5px solid rgba(0, 120, 212, 0.5); border-radius: 8px; }"
            )
        else:
            self.setStyleSheet("")

    # ------------------------------------------------------------------ #
    # 槽 — 串口控制
    # ------------------------------------------------------------------ #

    @Slot()
    def _refresh_ports(self) -> None:
        current = self._port_combo.currentText()
        self._port_combo.clear()
        ports = [p.portName() for p in QSerialPortInfo.availablePorts()]
        self._port_combo.addItems(ports)
        if current in ports:
            self._port_combo.setCurrentText(current)

    @Slot(bool)
    def _on_switch_changed(self, checked: bool) -> None:
        if checked:
            self._do_connect()
        else:
            self._do_disconnect()

    def _do_connect(self) -> None:
        port = self._port_combo.currentText()
        if not port:
            self._switch.setChecked(False)
            return
        config = PortConfig(port=port, baudrate=int(self._baud_combo.currentText()))
        parser_cls = BUILTIN_PARSERS[self._parser_combo.currentText()]
        self.worker = self._manager.create_worker(self._config.device_id, config, parser_cls(), self._repository)
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.frame_received.connect(self._on_frame)
        self._port_combo.setEnabled(False)
        self._baud_combo.setEnabled(False)
        self._parser_combo.setEnabled(False)
        asyncio.create_task(self.worker.connect())

    def _do_disconnect(self) -> None:
        if self.worker:
            asyncio.create_task(self.worker.disconnect())

    @Slot()
    def _on_connected(self) -> None:
        self._switch.setText("已连接")
        self._dot.set_connected(True)
        self._set_measurement_enabled(True)
        self.device_selected.emit(self._config.device_id)

    @Slot()
    def _on_disconnected(self) -> None:
        self._switch.setChecked(False)
        self._switch.setText("已断开")
        self._dot.set_connected(False)
        self._port_combo.setEnabled(True)
        self._baud_combo.setEnabled(True)
        self._parser_combo.setEnabled(True)
        self._set_measurement_enabled(False)
        self._manager.remove_worker(self._config.device_id)
        self.worker = None
        # 重置测量状态
        self._state = MeasurementState()
        self._display.reset()
        self.device_selected.emit(self._config.device_id)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._switch.setChecked(False)
        self._switch.setText("已断开")
        self._port_combo.setEnabled(True)
        self._baud_combo.setEnabled(True)
        self._parser_combo.setEnabled(True)
        InfoBar.error(
            title="串口错误",
            content=f"[{self._config.name}] {msg}",
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000,
            parent=self.window(),
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        self.device_selected.emit(self._config.device_id)

    # ------------------------------------------------------------------ #
    # 槽 — 测量操作
    # ------------------------------------------------------------------ #

    def _build_read_frame(self) -> bytes | None:
        """将命令输入框内容解析为带CRC的完整帧，失败返回 None。"""
        hex_text = self._cmd_edit.text().replace(" ", "").replace(":", "")
        if not hex_text:
            return None
        try:
            payload = bytes.fromhex(hex_text)
        except ValueError:
            InfoBar.warning(
                title="指令格式错误",
                content="请输入有效的十六进制字节，如 01 03 00 0D 00 04",
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000,
                parent=self.window(),
            )
            return None
        return build_modbus_frame(payload)

    @Slot()
    def _on_zero(self) -> None:
        """归零：发送一次读取，将响应值记为基准值。"""
        if not self.worker:
            return
        frame = self._build_read_frame()
        if frame is None:
            return
        self._state.zero_pending = True
        self._zero_btn.setEnabled(False)
        asyncio.create_task(self.worker.send(frame))

    @Slot()
    def _on_read(self) -> None:
        if not self.worker:
            return
        frame = self._build_read_frame()
        if frame is None:
            return
        asyncio.create_task(self.worker.send(frame))

    @Slot(object)
    def _on_frame(self, frame) -> None:
        from app.models.domain import Direction

        if frame.direction != Direction.RX:
            return
        parsed = frame.parsed
        if not parsed or parsed.get("type") != "distance":
            return
        value: float = parsed["distance_mm"]
        unit: str = parsed.get("unit", "mm")
        self._state.last_reading = value
        if self._state.zero_pending:
            self._state.baseline = value
            self._state.zero_pending = False
            self._zero_btn.setEnabled(True)
        self._update_display(unit)

    def _update_display(self, unit: str = "mm") -> None:
        r = self._state.last_reading
        b = self._state.baseline
        if b is not None and r is not None:
            self._display.set_absolute(b - r, b, unit)
        elif r is not None:
            self._display.set_relative(r, unit)


class DeviceListPanel(QWidget):
    """左侧设备列表面板，支持选中高亮和动态增删。"""

    device_selected = Signal(str)

    def __init__(
        self,
        device_configs: list[DeviceConfig],
        manager: SerialManager,
        repository: BaseRepository | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: dict[str, DeviceCard] = {}
        self._selected_id: str | None = None
        self._manager = manager
        self._repository = repository
        self._container_layout: QVBoxLayout | None = None
        self._build_ui(device_configs)

    def _build_ui(self, device_configs: list[DeviceConfig]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部标题栏
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background: transparent;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 8, 0)
        title = SubtitleLabel("设备列表")
        h_layout.addWidget(title)
        h_layout.addStretch()

        add_btn = ToolButton(FluentIcon.ADD)
        add_btn.setFixedSize(28, 28)
        add_btn.setToolTip("新增设备")
        add_btn.clicked.connect(self._on_add_clicked)
        h_layout.addWidget(add_btn)
        outer.addWidget(header)

        # 滚动区域
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("ScrollArea { border: none; background: transparent; }")
        outer.addWidget(scroll)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 4, 12, 12)
        self._container_layout = layout

        for cfg in device_configs:
            self._insert_card(cfg)

        layout.addStretch()
        scroll.setWidget(container)

    def _insert_card(self, cfg: DeviceConfig) -> DeviceCard:
        card = DeviceCard(cfg, self._manager, self._repository)
        card.device_selected.connect(self._on_card_selected)
        card.remove_requested.connect(self.remove_device)
        insert_pos = max(self._container_layout.count() - 1, 0)
        self._container_layout.insertWidget(insert_pos, card)
        self._cards[cfg.device_id] = card
        return card

    # ------------------------------------------------------------------ #
    # 公共方法
    # ------------------------------------------------------------------ #

    def add_device(self, cfg: DeviceConfig | None = None) -> str:
        if cfg is None:
            device_id = f"device_{uuid.uuid4().hex[:6]}"
            cfg = DeviceConfig(device_id=device_id, name=f"设备 {len(self._cards) + 1}")
        self._insert_card(cfg)
        return cfg.device_id

    @Slot(str)
    def remove_device(self, device_id: str) -> None:
        card = self._cards.pop(device_id, None)
        if card is None:
            return
        if card.worker:
            asyncio.create_task(card.worker.disconnect())
        self._container_layout.removeWidget(card)
        card.deleteLater()
        if self._selected_id == device_id:
            self._selected_id = None
            self.device_selected.emit(device_id)

    def get_config(self, device_id: str) -> DeviceConfig | None:
        card = self._cards.get(device_id)
        return card._config if card else None

    def get_card(self, device_id: str) -> DeviceCard | None:
        return self._cards.get(device_id)

    # ------------------------------------------------------------------ #
    # 槽
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_add_clicked(self) -> None:
        self.add_device()

    @Slot(str)
    def _on_card_selected(self, device_id: str) -> None:
        if self._selected_id and self._selected_id in self._cards:
            self._cards[self._selected_id].set_selected(False)
        self._selected_id = device_id
        if device_id in self._cards:
            self._cards[device_id].set_selected(True)
        self.device_selected.emit(device_id)
