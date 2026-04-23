"""设备列表面板与通用设备卡片。"""

import uuid
import asyncio

from PySide6.QtGui import QDrag, QColor, QPixmap
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtCore import Qt, Slot, QEvent, QPoint, Signal, QMimeData
from PySide6.QtWidgets import (
    QFrame,
    QWidget,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QStackedWidget,
)
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
    MessageBoxBase,
    InfoBarPosition,
    LargeTitleLabel,
    StrongBodyLabel,
    PrimaryPushButton,
    TransparentToolButton,
)

from app.logger import logger
from app.utils import build_modbus_frame
from app.serial.manager import SerialManager
from app.serial.parser import BUILTIN_PARSERS
from app.storage.repository import BaseRepository, SQLAlchemyRepository
from app.models.domain import PortConfig, DeviceConfig, MeasurementState

BAUDRATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

_COLOR_CONNECTED = "#0078d4"
_COLOR_DISCONNECTED = "#8a8a8a"

_BYTESIZE_OPTIONS = ["5", "6", "7", "8"]
_PARITY_OPTIONS = [("N", "无"), ("E", "偶"), ("O", "奇"), ("M", "标记"), ("S", "空格")]
_STOPBITS_OPTIONS = [("1", 1.0), ("1.5", 1.5), ("2", 2.0)]


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


class _DragHandle(TransparentToolButton):
    """拖拽排序手柄。"""

    drag_started = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIcon(FluentIcon.MOVE)
        self.setFixedSize(28, 28)
        self.setToolTip("拖拽排序")
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._press_pos: QPoint | None = None

    def mousePressEvent(self, e) -> None:  # type: ignore[override]
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # type: ignore[override]
        if self._press_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            self.drag_started.emit(drag)
            self._press_pos = None
            drag.exec(Qt.DropAction.MoveAction)
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # type: ignore[override]
        self._press_pos = None
        super().mouseReleaseEvent(e)


class _EditableLabel(QStackedWidget):
    """双击可编辑的标签控件。"""

    name_changed = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._committing = False
        self._editable = True
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._label = StrongBodyLabel(text)
        self._edit = LineEdit()
        self._edit.setFixedHeight(28)

        self.addWidget(self._label)
        self.addWidget(self._edit)

        self._label.installEventFilter(self)
        self._edit.returnPressed.connect(self._commit)
        self._edit.editingFinished.connect(self._on_editing_finished)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable

    def text(self) -> str:
        return self._label.text()

    def eventFilter(self, obj, event):
        if obj is self._label and event.type() == QEvent.Type.MouseButtonDblClick:
            if self._editable:
                self._start_edit()
            return True
        return super().eventFilter(obj, event)

    def _start_edit(self) -> None:
        self._edit.setText(self._label.text())
        self._edit.selectAll()
        self.setCurrentIndex(1)
        self._edit.setFocus()

    def _commit(self) -> None:
        if self._committing:
            return
        self._committing = True
        text = self._edit.text().strip()
        if text:
            self._label.setText(text)
            self.name_changed.emit(text)
        self.setCurrentIndex(0)
        self._committing = False

    def _on_editing_finished(self) -> None:
        if not self._committing and self.currentIndex() == 1:
            self._commit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.currentIndex() == 1:
            self._committing = True
            self.setCurrentIndex(0)
            self._committing = False
        else:
            super().keyPressEvent(event)


class _AdvancedPortDialog(MessageBoxBase):
    """高级串口参数配置弹窗。"""

    def __init__(self, port_config: PortConfig | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result_config: PortConfig | None = None
        self._port_config = port_config

        bytesize = port_config.bytesize if port_config else 8
        parity = port_config.parity if port_config else "N"
        stopbits = port_config.stopbits if port_config else 1.0

        title_label = SubtitleLabel("高级串口参数", self.widget)
        self.viewLayout.addWidget(title_label)
        self.viewLayout.addSpacing(8)

        self._bytesize_combo = ComboBox(self.widget)
        self._bytesize_combo.addItems(_BYTESIZE_OPTIONS)
        self._bytesize_combo.setCurrentText(str(bytesize))
        self.viewLayout.addLayout(self._field_row("数据位", self._bytesize_combo))

        self._parity_combo = ComboBox(self.widget)
        for code, label in _PARITY_OPTIONS:
            self._parity_combo.addItem(label, userData=code)
        current_parity_idx = next((i for i, (c, _) in enumerate(_PARITY_OPTIONS) if c == parity), 0)
        self._parity_combo.setCurrentIndex(current_parity_idx)
        self.viewLayout.addLayout(self._field_row("校验位", self._parity_combo))

        self._stopbits_combo = ComboBox(self.widget)
        for label, _ in _STOPBITS_OPTIONS:
            self._stopbits_combo.addItem(label)
        current_stop_idx = next((i for i, (_, v) in enumerate(_STOPBITS_OPTIONS) if v == stopbits), 0)
        self._stopbits_combo.setCurrentIndex(current_stop_idx)
        self.viewLayout.addLayout(self._field_row("停止位", self._stopbits_combo))

        self.widget.setMinimumWidth(300)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.yesButton.clicked.connect(self._on_ok)

    @staticmethod
    def _field_row(label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = BodyLabel(label_text)
        lbl.setFixedWidth(50)
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    def _on_ok(self) -> None:
        bytesize = int(self._bytesize_combo.currentText())
        parity = self._parity_combo.currentData()
        stopbits_text = self._stopbits_combo.currentText()
        stopbits = next(v for label, v in _STOPBITS_OPTIONS if label == stopbits_text)
        port = self._port_config.port if self._port_config else ""
        baudrate = self._port_config.baudrate if self._port_config else 9600
        self._result_config = PortConfig(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=str(parity) if parity is not None else "N",
            stopbits=stopbits,
        )

    def get_result(self) -> PortConfig | None:
        return self._result_config


class DeviceCard(CardWidget):
    """
    通用设备卡片，支持双串口配置（查询串口 + 阶跃串口）。
    """

    device_selected = Signal(str)
    remove_requested = Signal(str)
    measure_requested = Signal(str)
    cmd_changed = Signal(str, str)
    port_config_changed = Signal(str, object)  # device_id, query PortConfig | None
    step_port_config_changed = Signal(str, object)  # device_id, step PortConfig | None

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
        self.query_worker = None
        self.step_worker = None
        # 兼容：worker 指向 query_worker
        self.worker = None
        self._is_selected = False
        self._state = MeasurementState()
        self._disconnecting = False  # 主动断开标志，避免 _on_step_disconnected 误报
        self._is_dual_port = False  # 当前连接是否为双串口模式
        self._query_connected = False
        self._step_connected = False
        self._same_port = device_config.step_port_config is None

        self.setBorderRadius(8)
        self._build_ui()
        self._refresh_ports()
        self._restore_port_config()
        # 完成初始化后再连接 combo 信号
        self._port_combo.currentIndexChanged.connect(self._on_port_config_changed)
        self._baud_combo.currentIndexChanged.connect(self._on_port_config_changed)
        self._step_port_combo.currentIndexChanged.connect(self._on_step_port_config_changed)
        self._step_baud_combo.currentIndexChanged.connect(self._on_step_port_config_changed)

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── 标题行 ──────────────────────────────────────────────────────
        header = QHBoxLayout()

        self._drag_handle = _DragHandle()
        self._drag_handle.drag_started.connect(self._on_drag_started)
        header.addWidget(self._drag_handle)
        header.addSpacing(4)

        self._dot = StatusDot()
        header.addWidget(self._dot)
        header.addSpacing(6)

        self._name_label = _EditableLabel(self._config.name)
        self._name_label.name_changed.connect(self._on_name_changed)
        header.addWidget(self._name_label)
        header.addStretch()

        self._advanced_btn = ToolButton(FluentIcon.SETTING)
        self._advanced_btn.setFixedSize(28, 28)
        self._advanced_btn.setToolTip("高级串口参数")
        self._advanced_btn.clicked.connect(self._on_advanced_port)
        header.addWidget(self._advanced_btn)

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

        # ── 查询串口配置 ──────────────────────────────────────────────────
        query_label = CaptionLabel("查询串口")
        query_label.setStyleSheet("color: #666; font-weight: bold;")
        root.addWidget(query_label)

        form = QVBoxLayout()
        form.setSpacing(6)
        form.addLayout(self._row("串口", self._make_port_combo()))
        form.addLayout(self._row("波特率", self._make_baud_combo()))
        form.addLayout(self._row("解析", self._make_parser_combo()))
        root.addLayout(form)

        # ── 阶跃串口配置 ──────────────────────────────────────────────────
        step_header = QHBoxLayout()
        step_label = CaptionLabel("阶跃串口")
        step_label.setStyleSheet("color: #666; font-weight: bold;")
        step_header.addWidget(step_label)
        step_header.addStretch()

        self._same_port_switch = SwitchButton("同查询串口")
        self._same_port_switch.setChecked(self._same_port)
        self._same_port_switch.checkedChanged.connect(self._on_same_port_changed)
        step_header.addWidget(self._same_port_switch)
        root.addLayout(step_header)

        self._step_form_widget = QWidget()
        step_form = QVBoxLayout(self._step_form_widget)
        step_form.setContentsMargins(0, 0, 0, 0)
        step_form.setSpacing(6)
        step_form.addLayout(self._row("串口", self._make_step_port_combo()))
        step_form.addLayout(self._row("波特率", self._make_step_baud_combo()))
        root.addWidget(self._step_form_widget)
        self._step_form_widget.setVisible(not self._same_port)

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
        self._cmd_edit.setPlaceholderText("读取指令（HEX，不含CRC）")
        self._cmd_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._cmd_edit.setText(self._config.read_cmd_hex)
        self._cmd_edit.editingFinished.connect(self._on_cmd_changed)
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

        self._measure_btn = PrimaryPushButton(FluentIcon.PLAY, "开始测量")
        self._measure_btn.clicked.connect(lambda: self.measure_requested.emit(self._config.device_id))
        root.addWidget(self._measure_btn)

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
        self._baud_combo.setCurrentText(str(self._config.port_config.baudrate if self._config.port_config else 9600))
        self._baud_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._baud_combo

    def _make_parser_combo(self) -> QWidget:
        self._parser_combo = ComboBox()
        self._parser_combo.addItems(list(BUILTIN_PARSERS.keys()))
        self._parser_combo.setCurrentText(self._config.parser_name)
        self._parser_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._parser_combo

    def _make_step_port_combo(self) -> QWidget:
        self._step_port_combo = ComboBox()
        self._step_port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._step_port_combo

    def _make_step_baud_combo(self) -> QWidget:
        self._step_baud_combo = ComboBox()
        self._step_baud_combo.addItems(BAUDRATES)
        spc = self._config.step_port_config
        self._step_baud_combo.setCurrentText(str(spc.baudrate if spc else 9600))
        self._step_baud_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._step_baud_combo

    def _restore_port_config(self) -> None:
        """应用启动时，若 DB 有保存的串口配置，回填到下拉框。"""
        pc = self._config.port_config
        if pc is not None:
            ports = [self._port_combo.itemText(i) for i in range(self._port_combo.count())]
            if pc.port in ports:
                self._port_combo.setCurrentText(pc.port)
            self._baud_combo.setCurrentText(str(pc.baudrate))

        spc = self._config.step_port_config
        if spc is not None:
            ports = [self._step_port_combo.itemText(i) for i in range(self._step_port_combo.count())]
            if spc.port in ports:
                self._step_port_combo.setCurrentText(spc.port)
            self._step_baud_combo.setCurrentText(str(spc.baudrate))
            self._same_port = False
            self._same_port_switch.setChecked(False)

    def _set_measurement_enabled(self, enabled: bool) -> None:
        self._zero_btn.setEnabled(enabled)
        self._read_btn.setEnabled(enabled)
        self._measure_btn.setEnabled(enabled)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """连接/断开时切换各控件可用状态。"""
        self._port_combo.setEnabled(enabled)
        self._baud_combo.setEnabled(enabled)
        self._parser_combo.setEnabled(enabled)
        self._name_label.set_editable(enabled)
        self._advanced_btn.setEnabled(enabled)
        self._same_port_switch.setEnabled(enabled)
        if not self._same_port:
            self._step_port_combo.setEnabled(enabled)
            self._step_baud_combo.setEnabled(enabled)

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
        step_current = self._step_port_combo.currentText()
        ports = [p.portName() for p in QSerialPortInfo.availablePorts()]

        self._port_combo.clear()
        self._port_combo.addItems(ports)
        if current in ports:
            self._port_combo.setCurrentText(current)

        self._step_port_combo.clear()
        self._step_port_combo.addItems(ports)
        if step_current in ports:
            self._step_port_combo.setCurrentText(step_current)

    @Slot()
    def _on_port_config_changed(self) -> None:
        port = self._port_combo.currentText()
        baudrate_text = self._baud_combo.currentText()
        if not port or not baudrate_text:
            return
        existing = self._config.port_config
        new_config = PortConfig(
            port=port,
            baudrate=int(baudrate_text),
            bytesize=existing.bytesize if existing else 8,
            parity=existing.parity if existing else "N",
            stopbits=existing.stopbits if existing else 1.0,
        )
        self._config.port_config = new_config
        self.port_config_changed.emit(self._config.device_id, new_config)

    @Slot()
    def _on_step_port_config_changed(self) -> None:
        if self._same_port:
            return
        port = self._step_port_combo.currentText()
        baudrate_text = self._step_baud_combo.currentText()
        if not port or not baudrate_text:
            return
        existing = self._config.step_port_config
        new_config = PortConfig(
            port=port,
            baudrate=int(baudrate_text),
            bytesize=existing.bytesize if existing else 8,
            parity=existing.parity if existing else "N",
            stopbits=existing.stopbits if existing else 1.0,
        )
        self._config.step_port_config = new_config
        self.step_port_config_changed.emit(self._config.device_id, new_config)

    @Slot(bool)
    def _on_same_port_changed(self, same: bool) -> None:
        self._same_port = same
        self._step_form_widget.setVisible(not same)
        if same:
            self._config.step_port_config = None
            self.step_port_config_changed.emit(self._config.device_id, None)
        else:
            # 切到双口模式时，新出现的 step 控件需按当前连接状态启用
            connected = self.query_worker is not None
            self._step_port_combo.setEnabled(not connected)
            self._step_baud_combo.setEnabled(not connected)
            # 立即基于当前下拉框值初始化并持久化 step 配置，避免用户不动下拉框
            # 直接连接时 UI 与持久化配置不一致（重启后退回同口模式）
            self._on_step_port_config_changed()

    @Slot()
    def _on_name_changed(self, name: str) -> None:
        self._config.name = name
        self.port_config_changed.emit(self._config.device_id, self._config.port_config)

    @Slot()
    def _on_advanced_port(self) -> None:
        if self.query_worker is not None:
            InfoBar.warning(
                title="请先断开连接",
                content="修改高级串口参数前请先断开设备连接。",
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000,
                parent=self.window(),
            )
            return
        dialog = _AdvancedPortDialog(self._config.port_config, parent=self)
        dialog.exec()
        result = dialog.get_result()
        if result is not None:
            self._config.port_config = result
            self.port_config_changed.emit(self._config.device_id, result)

    @Slot(bool)
    def _on_switch_changed(self, checked: bool) -> None:
        if checked:
            self._do_connect()
        else:
            self._do_disconnect()

    def _do_connect(self) -> None:
        query_port = self._port_combo.currentText()
        if not query_port:
            self._switch.setChecked(False)
            return

        # 构建查询串口配置
        existing = self._config.port_config
        query_config = PortConfig(
            port=query_port,
            baudrate=int(self._baud_combo.currentText()),
            bytesize=existing.bytesize if existing else 8,
            parity=existing.parity if existing else "N",
            stopbits=existing.stopbits if existing else 1.0,
        )

        # 构建阶跃串口配置
        step_config = None
        if not self._same_port:
            step_port = self._step_port_combo.currentText()
            if not step_port:
                InfoBar.warning(
                    title="连接失败",
                    content="双串口模式下必须选择阶跃串口。",
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=3000,
                    parent=self.window(),
                )
                self._switch.setChecked(False)
                return
            if step_port == query_port:
                InfoBar.warning(
                    title="连接失败",
                    content="双串口模式下，阶跃串口不能与查询串口相同。",
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=3000,
                    parent=self.window(),
                )
                self._switch.setChecked(False)
                return
            step_existing = self._config.step_port_config
            step_config = PortConfig(
                port=step_port,
                baudrate=int(self._step_baud_combo.currentText()),
                bytesize=step_existing.bytesize if step_existing else 8,
                parity=step_existing.parity if step_existing else "N",
                stopbits=step_existing.stopbits if step_existing else 1.0,
            )

        parser_cls = BUILTIN_PARSERS[self._parser_combo.currentText()]
        self.query_worker, self.step_worker = self._manager.create_workers(
            self._config.device_id,
            query_config,
            parser_cls(),
            step_config,
            parser_cls(),
            self._repository,
        )
        self.worker = self.query_worker

        # 连接信号
        self.query_worker.connected.connect(self._on_query_connected)
        self.query_worker.disconnected.connect(self._on_query_disconnected)
        self.query_worker.error_occurred.connect(self._on_error)
        self.query_worker.frame_received.connect(self._on_frame)

        if step_config is not None:
            self.step_worker.connected.connect(self._on_step_connected)
            self.step_worker.disconnected.connect(self._on_step_disconnected)
            self.step_worker.error_occurred.connect(self._on_error)

        self._set_controls_enabled(False)
        self._is_dual_port = step_config is not None
        self._query_connected = False
        self._step_connected = False

        # 连接两个串口
        asyncio.create_task(self.query_worker.connect())
        if step_config is not None:
            asyncio.create_task(self.step_worker.connect())

    def _do_disconnect(self) -> None:
        # 断开期间禁用开关，避免用户快速重连导致旧 worker 的 disconnected/error
        # 信号回调清掉新创建的 worker（参见 SerialManager.create_workers 注释）
        self._switch.setEnabled(False)
        self._disconnecting = True  # 标记主动断开，_on_step_disconnected 据此跳过告警
        if self.query_worker:
            asyncio.create_task(self.query_worker.disconnect())
        if self.step_worker and self.step_worker is not self.query_worker:
            asyncio.create_task(self.step_worker.disconnect())

    # 查询串口连接回调

    @Slot()
    def _on_query_connected(self) -> None:
        # 忽略旧 worker 的过期信号，以及主动断开/重连过程中的 late signal
        if self.sender() is not None and self.sender() is not self.query_worker:
            return
        if self._disconnecting:
            return
        self._query_connected = True
        if self._is_dual_port and not self._step_connected:
            # 双口模式：等阶跃口也连上再切换为已连接
            return
        self._finalize_connected()

    @Slot()
    def _on_query_disconnected(self) -> None:
        # 忽略旧 worker 发出的过期信号（快速重连场景）
        if self.sender() is not None and self.sender() is not self.query_worker:
            return
        self._switch.setChecked(False)
        self._switch.setText("已断开")
        self._switch.setEnabled(True)
        self._dot.set_connected(False)
        self._set_controls_enabled(True)
        self._set_measurement_enabled(False)
        self._disconnecting = False
        self._query_connected = False
        self._step_connected = False
        self._is_dual_port = False
        self._manager.remove_workers(self._config.device_id)
        self.query_worker = None
        self.step_worker = None
        self.worker = None
        self._state = MeasurementState()
        self._display.reset()
        self.device_selected.emit(self._config.device_id)

    # 阶跃串口连接回调

    @Slot()
    def _on_step_connected(self) -> None:
        spc = self._config.step_port_config
        port_name = spc.port if spc else "?"
        logger.info(f"[{self._config.device_id}] 阶跃串口 {port_name} 已连接")
        # 忽略旧 worker 的过期信号，以及主动断开/重连过程中的 late signal
        if self.sender() is not None and self.sender() is not self.step_worker:
            return
        if self._disconnecting:
            return
        self._step_connected = True
        if self._query_connected:
            self._finalize_connected()

    def _finalize_connected(self) -> None:
        """两个串口都已连接（或单口模式下查询口已连接），切换 UI 为已连接状态。"""
        self._switch.setText("已连接")
        self._dot.set_connected(True)
        self._set_measurement_enabled(True)
        self.device_selected.emit(self._config.device_id)

    @Slot()
    def _on_step_disconnected(self) -> None:
        logger.info(f"[{self._config.device_id}] 阶跃串口已断开")
        # 忽略旧 worker 或已不在双口模式下的过期信号
        sender = self.sender()
        is_dual = self.step_worker is not None and self.step_worker is not self.query_worker
        if not is_dual or sender is not self.step_worker:
            return
        # 主动断开流程中不弹告警、不联动断开（由 _on_query_disconnected 统一收尾）
        if self._disconnecting:
            return
        # 双串口模式下阶跃口意外掉线视为致命错误：测量流程无法继续，
        # 需要联动断开查询口并提示用户，避免界面仍显示「已连接」但实际不可测量
        if self.step_worker is not None and self.step_worker is not self.query_worker:
            InfoBar.warning(
                title="阶跃串口已断开",
                content=f"[{self._config.name}] 阶跃口异常断开，已自动断开整台设备",
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
                parent=self.window(),
            )
            # 走统一断开流程：query worker 断开后 _on_query_disconnected 会做全量清理
            if self.query_worker is not None:
                asyncio.create_task(self.query_worker.disconnect())

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        # 忽略旧 worker 发出的过期错误信号
        sender = self.sender()
        if sender is not None and sender is not self.query_worker and sender is not self.step_worker:
            return
        # 主动断开期间不弹错误提示（断开本身会触发清理）
        if self._disconnecting:
            return
        InfoBar.error(
            title="串口错误",
            content=f"[{self._config.name}] {msg}",
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000,
            parent=self.window(),
        )
        # 串口异常后统一走断开清理流程，避免 manager 中残留 worker
        # 或持有失效的串口引用，导致资源泄漏、重连失败或状态错乱。
        # 注意：connect() 失败时不会发 disconnected，需要这里手动触发清理；
        # 已连接后异常时 disconnect() 自身会 emit disconnected 触发 _on_query_disconnected。
        if self.query_worker is not None:
            self._do_disconnect()
        else:
            # 无 worker（例如连接尚未建立就失败），直接复位 UI
            self._on_query_disconnected()

    def _on_drag_started(self, drag: QDrag) -> None:
        mime = QMimeData()
        mime.setData("application/x-device-id", self._config.device_id.encode("utf-8"))
        drag.setMimeData(mime)
        pixmap = self.grab()
        transparent = QPixmap(pixmap.size())
        transparent.fill(Qt.GlobalColor.transparent)
        from PySide6.QtGui import QPainter

        painter = QPainter(transparent)
        painter.setOpacity(0.7)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        drag.setPixmap(transparent)
        drag.setHotSpot(self._drag_handle.pos())

    def mousePressEvent(self, e) -> None:  # type: ignore[override]
        super().mousePressEvent(e)
        self.device_selected.emit(self._config.device_id)

    # ------------------------------------------------------------------ #
    # 槽 — 测量操作
    # ------------------------------------------------------------------ #

    @Slot()
    def _on_cmd_changed(self) -> None:
        self._config.read_cmd_hex = self._cmd_edit.text().strip()
        self.cmd_changed.emit(self._config.device_id, self._config.read_cmd_hex)

    def _build_read_frame(self) -> bytes | None:
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
        if not self.query_worker:
            return
        frame = self._build_read_frame()
        if frame is None:
            return
        self._state.zero_pending = True
        self._zero_btn.setEnabled(False)
        asyncio.create_task(self.query_worker.send(frame))

    @Slot()
    def _on_read(self) -> None:
        if not self.query_worker:
            return
        frame = self._build_read_frame()
        if frame is None:
            return
        asyncio.create_task(self.query_worker.send(frame))

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
    """左侧设备列表面板，支持选中高亮、动态增删和拖拽排序。"""

    device_selected = Signal(str)
    measure_requested = Signal(str)

    def __init__(
        self,
        device_configs: list[DeviceConfig],
        manager: SerialManager,
        repository: BaseRepository | None = None,
        db_repo: SQLAlchemyRepository | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: dict[str, DeviceCard] = {}
        self._selected_id: str | None = None
        self._manager = manager
        self._repository = repository
        self._db_repo = db_repo
        self._container_layout: QVBoxLayout
        self._scroll_container: QWidget
        self._build_ui(device_configs)

    def _build_ui(self, device_configs: list[DeviceConfig]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

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

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("ScrollArea { border: none; background: transparent; }")
        outer.addWidget(scroll)

        viewport = scroll.viewport()
        viewport.setAcceptDrops(True)
        viewport.dragEnterEvent = self._drag_enter_event  # type: ignore[method-assign]
        viewport.dragMoveEvent = self._drag_move_event  # type: ignore[method-assign]
        viewport.dropEvent = self._drop_event  # type: ignore[method-assign]
        self._scroll_viewport = viewport

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 4, 12, 12)
        self._container_layout = layout
        self._scroll_container = container

        for cfg in device_configs:
            self._insert_card(cfg)

        layout.addStretch()
        scroll.setWidget(container)

    def _insert_card(self, cfg: DeviceConfig) -> DeviceCard:
        card = DeviceCard(cfg, self._manager, self._repository)
        card.device_selected.connect(self._on_card_selected)
        card.remove_requested.connect(self.remove_device)
        card.measure_requested.connect(self.measure_requested)
        card.cmd_changed.connect(self._on_card_cmd_changed)
        card.port_config_changed.connect(self._on_port_config_changed)
        card.step_port_config_changed.connect(self._on_step_port_config_changed)
        insert_pos = max(self._container_layout.count() - 1, 0)
        self._container_layout.insertWidget(insert_pos, card)
        self._cards[cfg.device_id] = card
        return card

    def _sort_order(self, device_id: str) -> int:
        return list(self._cards.keys()).index(device_id) if device_id in self._cards else 0

    # ------------------------------------------------------------------ #
    # 拖拽排序
    # ------------------------------------------------------------------ #

    def _drag_enter_event(self, event) -> None:
        if event.mimeData().hasFormat("application/x-device-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drag_move_event(self, event) -> None:
        if event.mimeData().hasFormat("application/x-device-id"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drop_event(self, event) -> None:
        if not event.mimeData().hasFormat("application/x-device-id"):
            event.ignore()
            return
        device_id = event.mimeData().data("application/x-device-id").data().decode("utf-8")
        cards_list = list(self._cards.values())
        src_idx = next((i for i, c in enumerate(cards_list) if c._config.device_id == device_id), None)
        if src_idx is None:
            event.ignore()
            return
        drop_pos = event.position().toPoint()
        target_idx = self._calc_drop_index(drop_pos, cards_list)
        if src_idx == target_idx or src_idx + 1 == target_idx:
            event.acceptProposedAction()
            return
        card = cards_list.pop(src_idx)
        if target_idx > src_idx:
            target_idx -= 1
        cards_list.insert(target_idx, card)
        self._cards = {c._config.device_id: c for c in cards_list}
        self._rebuild_layout()
        self._save_sort_orders()
        event.acceptProposedAction()

    def _calc_drop_index(self, pos, cards_list: list) -> int:
        for i, card in enumerate(cards_list):
            if pos.y() < card.geometry().center().y():
                return i
        return len(cards_list)

    def _rebuild_layout(self) -> None:
        while self._container_layout.count() > 0:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(self._scroll_container)
        for card in self._cards.values():
            self._container_layout.addWidget(card)
        self._container_layout.addStretch()

    def _save_sort_orders(self) -> None:
        if self._db_repo is None:
            return
        for i, card in enumerate(self._cards.values()):
            asyncio.create_task(
                self._db_repo.save_device(
                    card._config,
                    card._config.read_cmd_hex,
                    i,
                    card._config.port_config,
                    card._config.step_port_config,
                )
            )

    # ------------------------------------------------------------------ #
    # 公共方法
    # ------------------------------------------------------------------ #

    def add_device(self, cfg: DeviceConfig | None = None) -> str:
        if cfg is None:
            device_id = f"device_{uuid.uuid4().hex[:6]}"
            cfg = DeviceConfig(device_id=device_id, name=f"设备 {len(self._cards) + 1}")
        self._insert_card(cfg)
        if self._db_repo is not None:
            order = self._sort_order(cfg.device_id)
            asyncio.create_task(
                self._db_repo.save_device(
                    cfg, cfg.read_cmd_hex, order, cfg.port_config, cfg.step_port_config
                )
            )
        return cfg.device_id

    @Slot(str)
    def remove_device(self, device_id: str) -> None:
        card = self._cards.pop(device_id, None)
        if card is None:
            return
        if card.query_worker:
            asyncio.create_task(card.query_worker.disconnect())
        if card.step_worker and card.step_worker is not card.query_worker:
            asyncio.create_task(card.step_worker.disconnect())
        self._container_layout.removeWidget(card)
        card.deleteLater()
        if self._db_repo is not None:
            asyncio.create_task(self._db_repo.delete_device(device_id))
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

    @Slot(str, str)
    def _on_card_cmd_changed(self, device_id: str, new_hex: str) -> None:
        if self._db_repo is None:
            return
        card = self._cards.get(device_id)
        if card is None:
            return
        asyncio.create_task(
            self._db_repo.save_device(
                card._config, new_hex, self._sort_order(device_id),
                card._config.port_config, card._config.step_port_config,
            )
        )

    @Slot(str, object)
    def _on_port_config_changed(self, device_id: str, port_config) -> None:
        if self._db_repo is None:
            return
        card = self._cards.get(device_id)
        if card is None:
            return
        asyncio.create_task(
            self._db_repo.save_device(
                card._config, card._config.read_cmd_hex, self._sort_order(device_id),
                port_config, card._config.step_port_config,
            )
        )

    @Slot(str, object)
    def _on_step_port_config_changed(self, device_id: str, step_port_config) -> None:
        if self._db_repo is None:
            return
        card = self._cards.get(device_id)
        if card is None:
            return
        asyncio.create_task(
            self._db_repo.save_device(
                card._config, card._config.read_cmd_hex, self._sort_order(device_id),
                card._config.port_config, step_port_config,
            )
        )

    @Slot(str)
    def _on_card_selected(self, device_id: str) -> None:
        if self._selected_id and self._selected_id in self._cards:
            self._cards[self._selected_id].set_selected(False)
        self._selected_id = device_id
        if device_id in self._cards:
            self._cards[device_id].set_selected(True)
        self.device_selected.emit(device_id)
