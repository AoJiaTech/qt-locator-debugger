from __future__ import annotations

from dataclasses import replace

from croniter import croniter
from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    ToolButton,
)

from app.schedule.manager import ScheduleManager, TimeWindow

_WEEKDAY_OPTIONS: list[tuple[str, int, str]] = [
    ("mon", 1, "周一"),
    ("tue", 2, "周二"),
    ("wed", 3, "周三"),
    ("thu", 4, "周四"),
    ("fri", 5, "周五"),
    ("sat", 6, "周六"),
    ("sun", 0, "周日"),
]
_WEEKDAY_NAME_MAP = {value: label for _, value, label in _WEEKDAY_OPTIONS}


class TimeWindowDialog(MessageBoxBase):
    """时间窗编辑弹窗。"""

    def __init__(self, window: TimeWindow | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: TimeWindow | None = None
        self._source_window = window
        self._weekday_checks: dict[int, CheckBox] = {}

        title = SubtitleLabel("编辑时间窗" if window is not None else "新增时间窗", self.widget)
        self.viewLayout.addWidget(title)
        self.viewLayout.addSpacing(8)

        self._name_edit = LineEdit(self.widget)
        self._name_edit.setPlaceholderText("可选名称，例如：工作日白班")
        self.viewLayout.addLayout(self._field_row("名称", self._name_edit))

        self._mode_switch = SwitchButton("简单模式")
        self._mode_switch.setChecked(True)
        self._mode_switch.checkedChanged.connect(self._on_mode_changed)
        self.viewLayout.addLayout(self._field_row("模式", self._mode_switch))

        self._stack = QStackedWidget(self.widget)
        self._stack.addWidget(self._build_simple_page())
        self._stack.addWidget(self._build_advanced_page())
        self.viewLayout.addWidget(self._stack)

        self._preview_label = CaptionLabel("Cron 预览: —", self.widget)
        self._preview_label.setWordWrap(True)
        self.viewLayout.addWidget(self._preview_label)

        self.widget.setMinimumWidth(480)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self._load_window(window)
        self._refresh_preview()

    def get_result(self) -> TimeWindow | None:
        return self._result

    def _build_simple_page(self) -> QWidget:
        page = QWidget(self.widget)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        time_row = QHBoxLayout()
        time_row.setSpacing(12)

        self._start_time_edit = QTimeEdit(page)
        self._start_time_edit.setDisplayFormat("HH:mm")
        self._start_time_edit.setTime(QTime(9, 0))
        self._start_time_edit.timeChanged.connect(self._refresh_preview)
        time_row.addLayout(self._field_row("开始", self._start_time_edit))

        self._end_time_edit = QTimeEdit(page)
        self._end_time_edit.setDisplayFormat("HH:mm")
        self._end_time_edit.setTime(QTime(18, 0))
        self._end_time_edit.timeChanged.connect(self._refresh_preview)
        time_row.addLayout(self._field_row("结束", self._end_time_edit))

        layout.addLayout(time_row)

        weekday_frame = QFrame(page)
        weekday_layout = QGridLayout(weekday_frame)
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_layout.setHorizontalSpacing(12)
        weekday_layout.setVerticalSpacing(8)
        for index, (_key, value, label) in enumerate(_WEEKDAY_OPTIONS):
            checkbox = CheckBox(label, weekday_frame)
            checkbox.setChecked(value in {1, 2, 3, 4, 5})
            checkbox.stateChanged.connect(self._refresh_preview)
            self._weekday_checks[value] = checkbox
            weekday_layout.addWidget(checkbox, index // 4, index % 4)
        layout.addWidget(weekday_frame)
        return page

    def _build_advanced_page(self) -> QWidget:
        page = QWidget(self.widget)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._start_cron_edit = LineEdit(page)
        self._start_cron_edit.setPlaceholderText("例如: 0 9 * * 1-5")
        self._start_cron_edit.textChanged.connect(self._refresh_preview)
        layout.addLayout(self._field_row("开始 Cron", self._start_cron_edit))

        self._end_cron_edit = LineEdit(page)
        self._end_cron_edit.setPlaceholderText("例如: 0 18 * * 1-5")
        self._end_cron_edit.textChanged.connect(self._refresh_preview)
        layout.addLayout(self._field_row("结束 Cron", self._end_cron_edit))
        return page

    @staticmethod
    def _field_row(label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        label = BodyLabel(label_text)
        label.setFixedWidth(72)
        row.addWidget(label)
        row.addWidget(widget)
        return row

    def _load_window(self, window: TimeWindow | None) -> None:
        if window is None:
            return

        self._name_edit.setText(window.label)
        parsed = self._try_parse_simple_window(window)
        if parsed is None:
            self._mode_switch.setChecked(False)
            self._stack.setCurrentIndex(1)
            self._start_cron_edit.setText(window.start_cron)
            self._end_cron_edit.setText(window.end_cron)
            return

        start_time, end_time, weekdays = parsed
        self._mode_switch.setChecked(True)
        self._stack.setCurrentIndex(0)
        self._start_time_edit.setTime(start_time)
        self._end_time_edit.setTime(end_time)
        for value, checkbox in self._weekday_checks.items():
            checkbox.setChecked(value in weekdays)

    def _on_mode_changed(self, checked: bool) -> None:
        self._mode_switch.setText("简单模式" if checked else "高级模式")
        self._stack.setCurrentIndex(0 if checked else 1)
        self._refresh_preview()

    def _selected_weekdays(self) -> list[int]:
        return [value for value, checkbox in self._weekday_checks.items() if checkbox.isChecked()]

    def _current_crons(self) -> tuple[str, str]:
        if self._mode_switch.isChecked():
            return self._generate_simple_crons()
        return self._start_cron_edit.text().strip(), self._end_cron_edit.text().strip()

    def _refresh_preview(self, *_args) -> None:
        start_cron, end_cron = self._current_crons()
        if not start_cron or not end_cron:
            self._preview_label.setText("Cron 预览: 请补全开始/结束表达式")
            return
        self._preview_label.setText(f"Cron 预览: 开始 {start_cron}    结束 {end_cron}")

    def _generate_simple_crons(self) -> tuple[str, str]:
        weekdays = self._selected_weekdays()
        weekday_expr = ",".join(str(day) for day in weekdays)
        start_time = self._start_time_edit.time()
        end_time = self._end_time_edit.time()
        start_cron = f"{start_time.minute()} {start_time.hour()} * * {weekday_expr}"
        end_cron = f"{end_time.minute()} {end_time.hour()} * * {weekday_expr}"
        return start_cron, end_cron

    def _build_label(self, start_cron: str, end_cron: str) -> str:
        custom_label = self._name_edit.text().strip()
        if custom_label:
            return custom_label
        if self._mode_switch.isChecked():
            weekday_text = "、".join(_WEEKDAY_NAME_MAP[day] for day in self._selected_weekdays())
            start_text = self._start_time_edit.time().toString("HH:mm")
            end_text = self._end_time_edit.time().toString("HH:mm")
            return f"{weekday_text} {start_text}-{end_text}"
        return f"{start_cron} -> {end_cron}"

    def validate(self) -> bool:
        start_cron, end_cron = self._current_crons()
        error = self._validate(start_cron, end_cron)
        if error is not None:
            self._result = None
            self._show_error(error)
            return False

        label = self._build_label(start_cron, end_cron)
        enabled = self._source_window.enabled if self._source_window is not None else True
        window_id = self._source_window.id if self._source_window is not None else TimeWindow("", "").id
        self._result = TimeWindow(
            id=window_id,
            label=label,
            enabled=enabled,
            start_cron=start_cron,
            end_cron=end_cron,
        )
        return True

    def _validate(self, start_cron: str, end_cron: str) -> str | None:
        if self._mode_switch.isChecked() and not self._selected_weekdays():
            return "请至少选择一个星期。"
        if not croniter.is_valid(start_cron, strict=True):
            return "开始 Cron 表达式无效。"
        if not croniter.is_valid(end_cron, strict=True):
            return "结束 Cron 表达式无效。"
        if start_cron == end_cron:
            return "开始和结束 Cron 表达式不能相同。"
        return None

    def _show_error(self, message: str) -> None:
        InfoBar.error(
            title="时间窗校验失败",
            content=message,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
            parent=self,
        )

    def _try_parse_simple_window(self, window: TimeWindow) -> tuple[QTime, QTime, set[int]] | None:
        start_parts = window.start_cron.split()
        end_parts = window.end_cron.split()
        if len(start_parts) != 5 or len(end_parts) != 5:
            return None
        if start_parts[2:4] != ["*", "*"] or end_parts[2:4] != ["*", "*"]:
            return None
        if start_parts[4] != end_parts[4]:
            return None

        weekdays = self._parse_weekday_expr(start_parts[4])
        if weekdays is None:
            return None

        try:
            start_time = QTime(int(start_parts[1]), int(start_parts[0]))
            end_time = QTime(int(end_parts[1]), int(end_parts[0]))
        except ValueError:
            return None

        if not start_time.isValid() or not end_time.isValid():
            return None
        return start_time, end_time, weekdays

    @staticmethod
    def _parse_weekday_expr(expr: str) -> set[int] | None:
        values: set[int] = set()
        for part in expr.split(","):
            token = part.strip()
            if not token:
                return None
            if "-" in token:
                bounds = token.split("-", 1)
                if len(bounds) != 2 or not all(item.isdigit() for item in bounds):
                    return None
                start, end = (int(item) for item in bounds)
                if start > end:
                    return None
                values.update(range(start, end + 1))
                continue
            if not token.isdigit():
                return None
            values.add(int(token))
        if not values or any(value not in _WEEKDAY_NAME_MAP for value in values):
            return None
        return values


class _TimeWindowCard(CardWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    enabled_changed = Signal(str, bool)

    def __init__(self, window: TimeWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self.setBorderRadius(8)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = StrongBodyLabel(_display_label(self._window))
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.addWidget(title)

        self._enabled_switch = SwitchButton("启用" if self._window.enabled else "停用")
        self._enabled_switch.setChecked(self._window.enabled)
        self._enabled_switch.checkedChanged.connect(self._on_enabled_changed)
        header.addWidget(self._enabled_switch)

        edit_button = ToolButton(FluentIcon.EDIT)
        edit_button.setToolTip("编辑时间窗")
        edit_button.clicked.connect(lambda: self.edit_requested.emit(self._window.id))
        header.addWidget(edit_button)

        delete_button = ToolButton(FluentIcon.DELETE)
        delete_button.setToolTip("删除时间窗")
        delete_button.clicked.connect(lambda: self.delete_requested.emit(self._window.id))
        header.addWidget(delete_button)

        root.addLayout(header)
        root.addWidget(CaptionLabel(f"开始: {self._window.start_cron}"))
        root.addWidget(CaptionLabel(f"结束: {self._window.end_cron}"))
        root.addWidget(CaptionLabel(f"说明: {_describe_window(self._window)}"))

    def _on_enabled_changed(self, checked: bool) -> None:
        self._enabled_switch.setText("启用" if checked else "停用")
        self.enabled_changed.emit(self._window.id, checked)


class SchedulePage(QWidget):
    """时间调度配置页面。"""

    def __init__(self, schedule_manager: ScheduleManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("schedulePage")
        self._schedule_manager = schedule_manager
        self._build_ui()
        self._schedule_manager.active_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        enabled = self._schedule_manager.is_enabled()
        self._global_switch.blockSignals(True)
        self._global_switch.setChecked(enabled)
        self._global_switch.setText("已启用" if enabled else "已关闭")
        self._global_switch.blockSignals(False)

        active = self._schedule_manager.is_active()
        self._status_value.setText("当前允许测量" if active else "当前处于暂停窗口")

        next_transition = self._schedule_manager.next_transition()
        if next_transition is None:
            self._next_value.setText("暂无下一次切换")
        else:
            next_active, when = next_transition
            action_text = "恢复允许" if next_active else "切换为暂停"
            self._next_value.setText(f"{when.strftime('%Y-%m-%d %H:%M')} · {action_text}")

        self._reload_window_cards()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        status_card = CardWidget()
        status_card.setBorderRadius(8)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(SubtitleLabel("调度设置"))
        top_row.addStretch()
        self._global_switch = SwitchButton("已关闭")
        self._global_switch.checkedChanged.connect(self._on_global_enabled_changed)
        top_row.addWidget(self._global_switch)
        status_layout.addLayout(top_row)

        self._status_value = BodyLabel("—")
        self._next_value = CaptionLabel("—")
        self._next_value.setWordWrap(True)
        status_layout.addLayout(self._info_row("当前状态", self._status_value))
        status_layout.addLayout(self._info_row("下次切换", self._next_value))
        root.addWidget(status_card)

        windows_card = CardWidget()
        windows_card.setBorderRadius(8)
        windows_layout = QVBoxLayout(windows_card)
        windows_layout.setContentsMargins(14, 12, 14, 12)
        windows_layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("时间窗列表"))
        header.addStretch()
        add_button = PrimaryPushButton(FluentIcon.ADD, "新增时间窗")
        add_button.clicked.connect(self._on_add_window)
        header.addWidget(add_button)
        windows_layout.addLayout(header)

        self._summary_label = CaptionLabel("—")
        windows_layout.addWidget(self._summary_label)

        self._scroll_area = ScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet("ScrollArea { border: none; background: transparent; }")
        self._window_container = QWidget()
        self._window_layout = QVBoxLayout(self._window_container)
        self._window_layout.setContentsMargins(0, 0, 0, 0)
        self._window_layout.setSpacing(10)
        self._window_layout.addStretch()
        self._scroll_area.setWidget(self._window_container)
        windows_layout.addWidget(self._scroll_area, stretch=1)

        root.addWidget(windows_card, stretch=1)

    @staticmethod
    def _info_row(label_text: str, value_widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        label = BodyLabel(label_text)
        label.setFixedWidth(72)
        row.addWidget(label)
        row.addWidget(value_widget, stretch=1)
        return row

    def _reload_window_cards(self) -> None:
        windows = self._schedule_manager.windows()
        self._summary_label.setText(f"共 {len(windows)} 个时间窗，启用 {sum(1 for window in windows if window.enabled)} 个")

        while self._window_layout.count() > 1:
            item = self._window_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not windows:
            empty = CaptionLabel("暂无时间窗配置，点击右上角按钮添加。")
            empty.setStyleSheet("color: #7f849c; padding: 8px 0;")
            self._window_layout.insertWidget(0, empty)
            return

        for window in windows:
            card = _TimeWindowCard(window)
            card.edit_requested.connect(self._on_edit_window)
            card.delete_requested.connect(self._on_delete_window)
            card.enabled_changed.connect(self._on_toggle_window)
            self._window_layout.insertWidget(self._window_layout.count() - 1, card)

    def _on_global_enabled_changed(self, checked: bool) -> None:
        self._schedule_manager.set_enabled(checked)
        self.refresh()

    def _on_add_window(self) -> None:
        dialog = TimeWindowDialog(parent=self)
        if not dialog.exec():
            return
        result = dialog.get_result()
        if result is None:
            return
        windows = self._schedule_manager.windows()
        windows.append(result)
        self._schedule_manager.set_windows(windows)
        self.refresh()

    def _on_edit_window(self, window_id: str) -> None:
        windows = self._schedule_manager.windows()
        current = next((window for window in windows if window.id == window_id), None)
        if current is None:
            return

        dialog = TimeWindowDialog(current, parent=self)
        if not dialog.exec():
            return
        result = dialog.get_result()
        if result is None:
            return

        updated = [result if window.id == window_id else window for window in windows]
        self._schedule_manager.set_windows(updated)
        self.refresh()

    def _on_delete_window(self, window_id: str) -> None:
        windows = [window for window in self._schedule_manager.windows() if window.id != window_id]
        self._schedule_manager.set_windows(windows)
        self.refresh()

    def _on_toggle_window(self, window_id: str, enabled: bool) -> None:
        updated = [replace(window, enabled=enabled) if window.id == window_id else window for window in self._schedule_manager.windows()]
        self._schedule_manager.set_windows(updated)
        self.refresh()


def _display_label(window: TimeWindow) -> str:
    return window.label.strip() or _describe_window(window)


def _describe_window(window: TimeWindow) -> str:
    parsed = _try_describe_simple_window(window)
    if parsed is not None:
        weekday_text, start_text, end_text = parsed
        return f"{weekday_text} {start_text}-{end_text}"
    return "高级 Cron 时间窗"


def _try_describe_simple_window(window: TimeWindow) -> tuple[str, str, str] | None:
    start_parts = window.start_cron.split()
    end_parts = window.end_cron.split()
    if len(start_parts) != 5 or len(end_parts) != 5:
        return None
    if start_parts[2:4] != ["*", "*"] or end_parts[2:4] != ["*", "*"]:
        return None
    if start_parts[4] != end_parts[4]:
        return None
    weekdays = TimeWindowDialog._parse_weekday_expr(start_parts[4])
    if weekdays is None:
        return None
    weekday_text = "、".join(_WEEKDAY_NAME_MAP[day] for day in sorted(weekdays, key=lambda value: (value == 0, value)))
    try:
        start_time = QTime(int(start_parts[1]), int(start_parts[0]))
        end_time = QTime(int(end_parts[1]), int(end_parts[0]))
    except ValueError:
        return None
    if not start_time.isValid() or not end_time.isValid():
        return None
    return weekday_text, start_time.toString("HH:mm"), end_time.toString("HH:mm")
