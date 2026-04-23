"""历史测量查询页。"""

import asyncio
from typing import Any
from datetime import datetime
from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    InfoBar,
    ComboBox,
    BodyLabel,
    CardWidget,
    FluentIcon,
    PushButton,
    ScrollArea,
    CaptionLabel,
    InfoBarPosition,
    StrongBodyLabel,
    PrimaryPushButton,
)

from app.serial.manager import SerialManager
from app.storage.repository import SQLAlchemyRepository

pg.setConfigOption("background", "k")
pg.setConfigOption("foreground", "w")
pg.setConfigOptions(antialias=True)


@dataclass(slots=True)
class _SessionCardState:
    session_id: int
    device_id: str
    device_name: str
    mode: str
    start_time: datetime
    end_time: datetime | None
    cycle_count: int


class _SessionCard(CardWidget):
    session_selected = Signal(int)

    def __init__(self, session: _SessionCardState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session
        self.setBorderRadius(8)
        self.setFixedWidth(220)
        self._build_ui()
        self.set_selected(False)

    @property
    def session_id(self) -> int:
        return self._session.session_id

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "_SessionCard { background-color: rgba(0, 120, 212, 0.10); "
                "border: 1.5px solid rgba(0, 120, 212, 0.5); border-radius: 8px; }"
            )
            return
        self.setStyleSheet("")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.session_selected.emit(self._session.session_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = StrongBodyLabel(self._session.device_name)
        header.addWidget(title)
        header.addStretch()
        mode_label = CaptionLabel(self._mode_text(self._session.mode))
        mode_label.setStyleSheet("color: #89b4fa;")
        header.addWidget(mode_label)
        layout.addLayout(header)

        layout.addWidget(CaptionLabel(f"开始时间: {self._session.start_time.strftime('%Y-%m-%d %H:%M:%S')}"))
        layout.addWidget(CaptionLabel(f"周期数: {self._session.cycle_count}"))

        if self._session.end_time is None:
            unfinished = CaptionLabel("未完成")
            unfinished.setStyleSheet(
                "background-color: rgba(255, 165, 0, 0.18); color: #fab387; padding: 2px 8px; border-radius: 8px;"
            )
            layout.addWidget(unfinished, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
            layout.addWidget(
                CaptionLabel(f"时长: {self._format_duration(self._session.start_time, self._session.end_time)}")
            )

        layout.addStretch()

    @staticmethod
    def _mode_text(mode: str) -> str:
        return "单次" if mode == "single" else "自动"

    @staticmethod
    def _format_duration(start_time: datetime, end_time: datetime) -> str:
        seconds = max(0, int((end_time - start_time).total_seconds()))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        if hours > 0:
            return f"{hours}时{minutes}分{secs}秒"
        if minutes > 0:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"


class HistoryPage(QWidget):
    """历史测量会话查询与波形回放页面。"""

    resume_requested = Signal(int, str)

    def __init__(
        self,
        repository: SQLAlchemyRepository,
        serial_manager: SerialManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        self._repository = repository
        self._serial_manager = serial_manager
        self._device_name_map: dict[str, str] = {}
        self._cards: dict[int, _SessionCard] = {}
        self._session_states: dict[int, _SessionCardState] = {}
        self._selected_session_id: int | None = None
        self._selected_resume_payload: dict[str, Any] | None = None
        self._build_ui()
        asyncio.create_task(self.reload())

    async def reload(self) -> None:
        devices = await self._repository.load_devices()
        self._device_name_map = {device.device_id: device.name for device in devices}
        self._populate_device_filter(devices)
        await self._reload_sessions()

    def get_selected_resume_payload(self) -> dict[str, Any] | None:
        """返回当前选中会话的恢复测量参数。"""
        if self._selected_resume_payload is None:
            return None
        return dict(self._selected_resume_payload)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        top_card = CardWidget()
        top_card.setBorderRadius(8)
        top_layout = QVBoxLayout(top_card)
        top_layout.setContentsMargins(12, 10, 12, 10)
        top_layout.setSpacing(10)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(BodyLabel("设备"))
        self._device_filter = ComboBox()
        self._device_filter.setMinimumWidth(220)
        self._device_filter.currentIndexChanged.connect(lambda _index: asyncio.create_task(self._reload_sessions()))
        filter_row.addWidget(self._device_filter)

        filter_row.addWidget(BodyLabel("模式"))
        self._mode_filter = ComboBox()
        self._mode_filter.addItem("全部", userData=None)
        self._mode_filter.addItem("单次", userData="single")
        self._mode_filter.addItem("自动", userData="auto")
        self._mode_filter.currentIndexChanged.connect(lambda _index: asyncio.create_task(self._reload_sessions()))
        filter_row.addWidget(self._mode_filter)

        filter_row.addStretch()
        self._refresh_btn = PushButton(FluentIcon.SYNC, "刷新")
        self._refresh_btn.clicked.connect(lambda: asyncio.create_task(self.reload()))
        filter_row.addWidget(self._refresh_btn)
        top_layout.addLayout(filter_row)

        self._session_scroll = ScrollArea()
        self._session_scroll.setWidgetResizable(True)
        self._session_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._session_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._session_scroll.setStyleSheet("ScrollArea { border: none; background: transparent; }")

        self._session_container = QWidget()
        self._session_layout = QHBoxLayout(self._session_container)
        self._session_layout.setContentsMargins(0, 0, 0, 0)
        self._session_layout.setSpacing(10)
        self._session_layout.addStretch()
        self._session_scroll.setWidget(self._session_container)
        top_layout.addWidget(self._session_scroll)

        root.addWidget(top_card)

        bottom_card = CardWidget()
        bottom_card.setBorderRadius(8)
        bottom_layout = QVBoxLayout(bottom_card)
        bottom_layout.setContentsMargins(12, 10, 12, 10)
        bottom_layout.setSpacing(8)

        info_row = QHBoxLayout()
        info_row.setSpacing(16)
        self._selection_label = CaptionLabel("当前会话: 未选择")
        info_row.addWidget(self._selection_label)
        self._meta_label = CaptionLabel("参数: —")
        info_row.addWidget(self._meta_label)
        info_row.addStretch()
        bottom_layout.addLayout(info_row)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("#11111b")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setLabel("left", "百分比 (%)")
        self._plot_widget.setLabel("bottom", "时间 (s)")
        self._plot_widget.setYRange(0, 105, padding=0)
        self._plot_widget.setLimits(xMin=0, yMin=0, yMax=105)
        self._plot_widget.setMouseEnabled(x=True, y=False)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.addLegend(offset=(10, 10))
        self._curve_current = self._plot_widget.plot(
            [], [], pen=pg.mkPen(color="#89b4fa", width=2), name="电流阶跃 %", stepMode="right"
        )
        self._curve_distance = self._plot_widget.plot([], [], pen=pg.mkPen(color="#a6e3a1", width=2), name="位移 %")
        bottom_layout.addWidget(self._plot_widget, stretch=1)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self._clear_btn = PushButton(FluentIcon.DELETE, "清空波形")
        self._clear_btn.clicked.connect(self._clear_plot)
        button_row.addWidget(self._clear_btn)

        self._continue_btn = PrimaryPushButton(FluentIcon.PLAY, "继续")
        self._continue_btn.clicked.connect(self._on_continue_clicked)
        button_row.addWidget(self._continue_btn)
        button_row.addStretch()
        bottom_layout.addLayout(button_row)

        root.addWidget(bottom_card, stretch=1)

    def _populate_device_filter(self, devices) -> None:
        current_device_id = self._device_filter.currentData()
        self._device_filter.blockSignals(True)
        self._device_filter.clear()
        self._device_filter.addItem("全部设备", userData=None)
        for device in devices:
            self._device_filter.addItem(device.name, userData=device.device_id)

        target_index = 0
        if current_device_id is not None:
            for index in range(self._device_filter.count()):
                if self._device_filter.itemData(index) == current_device_id:
                    target_index = index
                    break
        self._device_filter.setCurrentIndex(target_index)
        self._device_filter.blockSignals(False)

    async def _reload_sessions(self) -> None:
        device_id = self._device_filter.currentData()
        mode = self._mode_filter.currentData()
        sessions = await self._repository.list_sessions(device_id=device_id, mode=mode)

        self._clear_session_cards()
        self._selected_session_id = None
        self._selected_resume_payload = None
        self._clear_plot()
        self._selection_label.setText("当前会话: 未选择")
        self._meta_label.setText("参数: —")

        if not sessions:
            empty = CaptionLabel("暂无匹配的测量会话")
            empty.setStyleSheet("color: #7f849c; padding: 12px;")
            self._session_layout.insertWidget(0, empty)
            return

        for session in sessions:
            state = _SessionCardState(
                session_id=session.id,
                device_id=session.device_id,
                device_name=self._device_name_map.get(session.device_id, session.device_id),
                mode=session.mode,
                start_time=session.start_time,
                end_time=session.end_time,
                cycle_count=session.cycle_count,
            )
            self._session_states[session.id] = state
            card = _SessionCard(state)
            card.session_selected.connect(
                lambda session_id, sid=session.id: asyncio.create_task(self._select_session(sid))
            )
            self._cards[session.id] = card
            self._session_layout.insertWidget(self._session_layout.count() - 1, card)

        first_session_id = sessions[0].id
        await self._select_session(first_session_id)

    def _clear_session_cards(self) -> None:
        self._cards.clear()
        self._session_states.clear()
        while self._session_layout.count() > 1:
            item = self._session_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    async def _select_session(self, session_id: int) -> None:
        session = await self._repository.get_session(session_id)
        if session is None:
            self._selected_session_id = None
            self._selected_resume_payload = None
            self._clear_plot()
            self._selection_label.setText("当前会话: 未找到")
            self._meta_label.setText("参数: —")
            return

        for card_id, card in self._cards.items():
            card.set_selected(card_id == session_id)

        points = await self._repository.get_session_points(session_id)
        self._selected_session_id = session_id
        self._selected_resume_payload = self._build_resume_payload(session, points)
        self._render_points(points)

        device_name = self._device_name_map.get(session.device_id, session.device_id)
        status_text = "未完成" if session.end_time is None else "已完成"
        self._selection_label.setText(f"当前会话: {device_name} / {self._mode_text(session.mode)} / {status_text}")
        self._meta_label.setText(
            f"参数: 周期 {session.step_period_s:g}s, 采样 {session.sample_interval_ms}ms,"
            f" 峰值 {session.displacement_peak_mm:g}mm"
        )

    def _build_resume_payload(self, session, points) -> dict[str, Any]:
        history_time = [float(p.elapsed_s) for p in points]
        history_current = [float(p.current_pct) for p in points]
        history_distance = [float(p.distance_pct) for p in points]

        last_step_index = int(points[-1].step_index) if points else 0
        paused_step_index = session.paused_step_index
        step_index = int(paused_step_index) if paused_step_index is not None else last_step_index

        return {
            "session_id": int(session.id),
            "device_id": str(session.device_id),
            "step_index": step_index,
            "cycle_count": int(session.cycle_count),
            "time_offset": history_time[-1] if history_time else 0.0,
            "history_time": history_time,
            "history_current": history_current,
            "history_distance": history_distance,
            "step_period_s": float(session.step_period_s),
            "sample_interval_ms": int(session.sample_interval_ms),
            "displacement_peak_mm": float(session.displacement_peak_mm),
            "mode": str(session.mode),
            "baseline_distance_mm": float(session.baseline_distance_mm)
            if session.baseline_distance_mm is not None
            else None,
        }

    def _render_points(self, points) -> None:
        if not points:
            self._clear_plot()
            return

        time_data = [float(p.elapsed_s) for p in points]
        current_data = [float(point.current_pct) for point in points]
        distance_data = [float(point.distance_pct) for point in points]

        self._curve_current.setData(time_data, current_data)
        self._curve_distance.setData(time_data, distance_data)
        self._plot_widget.setXRange(0, max(1.0, time_data[-1]), padding=0)

    def _clear_plot(self) -> None:
        self._curve_current.setData([], [])
        self._curve_distance.setData([], [])
        self._plot_widget.setXRange(0, 1.0, padding=0)

    def _on_continue_clicked(self) -> None:
        payload = self.get_selected_resume_payload()
        if payload is None:
            InfoBar.warning(
                title="未选择会话",
                content="请先选择要继续的历史测量会话。",
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000,
                parent=self.window(),
            )
            return

        query_worker, _step_worker = self._serial_manager.get_device_workers(payload["device_id"])
        if query_worker is None:
            InfoBar.warning(
                title="设备未连接",
                content="当前会话对应设备未连接，无法继续测量。",
                position=InfoBarPosition.TOP_RIGHT,
                duration=3000,
                parent=self.window(),
            )
            return

        self.resume_requested.emit(int(payload["session_id"]), str(payload["device_id"]))

    @staticmethod
    def _mode_text(mode: str) -> str:
        return "单次" if mode == "single" else "自动"
