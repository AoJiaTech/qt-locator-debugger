import asyncio

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from PySide6.QtSerialPort import QSerialPortInfo
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    Theme,
    ToolButton,
    setTheme,
)

from app.models.domain import DeviceConfig, PortConfig
from app.serial.manager import SerialManager
from app.serial.parser import BUILTIN_PARSERS
from app.storage.repository import BaseRepository

BAUDRATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

# 连接状态指示色
_COLOR_CONNECTED = "#0078d4"      # Fluent blue
_COLOR_DISCONNECTED = "#8a8a8a"   # grey


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
        from PySide6.QtGui import QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(_COLOR_CONNECTED if self._connected else _COLOR_DISCONNECTED)
        painter.setBrush(color)
        painter.setPen(QPen(color.darker(120), 1))
        painter.drawEllipse(1, 1, 8, 8)


class DeviceCard(CardWidget):
    """
    单设备 Fluent 卡片：串口选择、波特率、连接控制、设备专属操作区。
    点击卡片 → 发出 device_selected；连接/断开 → 同样发出 device_selected。
    子类覆盖 _build_device_actions() 添加设备专属按钮。
    """

    device_selected = Signal(str)   # device_id

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

        # 刷新串口按钮（图标按钮）
        self._refresh_btn = ToolButton(FluentIcon.SYNC)
        self._refresh_btn.setFixedSize(28, 28)
        self._refresh_btn.setToolTip("刷新串口列表")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        header.addWidget(self._refresh_btn)
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

        # ── 分隔线 + 设备专属操作区 ──────────────────────────────────────
        self._actions_layout = QVBoxLayout()
        self._actions_layout.setSpacing(6)
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        self._sep.setStyleSheet("color: rgba(0,0,0,0.08);")
        self._sep.hide()
        root.addWidget(self._sep)
        root.addLayout(self._actions_layout)
        self._build_device_actions()

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
        self._baud_combo.setCurrentText(
            str(self._config.port_config.baudrate if self._config.port_config else 115200)
        )
        self._baud_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._baud_combo

    def _make_parser_combo(self) -> QWidget:
        self._parser_combo = ComboBox()
        self._parser_combo.addItems(list(BUILTIN_PARSERS.keys()))
        self._parser_combo.setCurrentText(self._config.parser_name)
        self._parser_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return self._parser_combo

    def _build_device_actions(self) -> None:
        """子类覆盖此方法，向 self._actions_layout 添加设备专属控件，并调用 self._sep.show()。"""
        pass

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
    # 槽
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
        self.worker = self._manager.create_worker(
            self._config.device_id, config, parser_cls(), self._repository
        )
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.error_occurred.connect(self._on_error)
        # 锁定配置项，防止连接时修改
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
        self.device_selected.emit(self._config.device_id)

    @Slot()
    def _on_disconnected(self) -> None:
        self._switch.setChecked(False)
        self._switch.setText("已断开")
        self._dot.set_connected(False)
        self._port_combo.setEnabled(True)
        self._baud_combo.setEnabled(True)
        self._parser_combo.setEnabled(True)
        self._manager.remove_worker(self._config.device_id)
        self.worker = None
        self.device_selected.emit(self._config.device_id)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        # 连接失败：复原开关
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


class DeviceListPanel(QWidget):
    """左侧设备列表面板，支持选中高亮。"""

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
        self._build_ui(device_configs, manager, repository)

    def _build_ui(
        self,
        device_configs: list[DeviceConfig],
        manager: SerialManager,
        repository: BaseRepository | None,
    ) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部标题栏
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background: transparent;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        title = SubtitleLabel("设备列表")
        h_layout.addWidget(title)
        h_layout.addStretch()
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

        for cfg in device_configs:
            card = DeviceCard(cfg, manager, repository)
            card.device_selected.connect(self._on_card_selected)
            layout.addWidget(card)
            self._cards[cfg.device_id] = card

        layout.addStretch()
        scroll.setWidget(container)

    @Slot(str)
    def _on_card_selected(self, device_id: str) -> None:
        # 更新选中高亮
        if self._selected_id and self._selected_id in self._cards:
            self._cards[self._selected_id].set_selected(False)
        self._selected_id = device_id
        self._cards[device_id].set_selected(True)
        self.device_selected.emit(device_id)

    def get_card(self, device_id: str) -> DeviceCard | None:
        return self._cards.get(device_id)
