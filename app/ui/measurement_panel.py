"""测量面板：参数设置行 + PyQtGraph 波形图 + 状态栏。"""

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QFileDialog, QDoubleSpinBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)

if TYPE_CHECKING:
    from app.measurement.controller import MeasurementController

pg.setConfigOption("background", "k")
pg.setConfigOption("foreground", "w")
pg.setConfigOptions(antialias=True)


class MeasurementPanel(QWidget):
    """单设备测量面板。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller: MeasurementController | None = None
        self._time_data: list[float] = []
        self._current_data: list[float] = []
        self._distance_data: list[float] = []
        self._last_mode = "single"
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        params_card = CardWidget()
        params_card.setBorderRadius(8)
        params_layout = QHBoxLayout(params_card)
        params_layout.setContentsMargins(12, 8, 12, 8)
        params_layout.setSpacing(10)

        params_layout.addWidget(BodyLabel("阶跃周期"))
        self._period_spin = QDoubleSpinBox()
        self._period_spin.setRange(0.1, 60.0)
        self._period_spin.setValue(2.0)
        self._period_spin.setSingleStep(0.5)
        self._period_spin.setFixedWidth(80)
        params_layout.addWidget(self._period_spin)
        params_layout.addWidget(CaptionLabel("s"))

        params_layout.addWidget(BodyLabel("采样间隔"))
        self._sample_spin = SpinBox()
        self._sample_spin.setRange(100, 5000)
        self._sample_spin.setValue(200)
        self._sample_spin.setSingleStep(100)
        self._sample_spin.setFixedWidth(80)
        params_layout.addWidget(self._sample_spin)
        params_layout.addWidget(CaptionLabel("ms"))

        params_layout.addWidget(BodyLabel("位移峰值"))
        self._peak_spin = QDoubleSpinBox()
        self._peak_spin.setRange(0.1, 9999.0)
        self._peak_spin.setValue(50.0)
        self._peak_spin.setSingleStep(1.0)
        self._peak_spin.setFixedWidth(90)
        params_layout.addWidget(self._peak_spin)
        params_layout.addWidget(CaptionLabel("mm"))

        params_layout.addStretch()

        self._single_btn = PrimaryPushButton(FluentIcon.PLAY, "单次")
        self._single_btn.setFixedWidth(88)
        self._single_btn.clicked.connect(self._on_single)
        params_layout.addWidget(self._single_btn)

        self._auto_btn = PushButton(FluentIcon.SYNC, "自动")
        self._auto_btn.setFixedWidth(88)
        self._auto_btn.clicked.connect(self._on_auto)
        params_layout.addWidget(self._auto_btn)

        self._stop_btn = PushButton(FluentIcon.PAUSE, "停止")
        self._stop_btn.setFixedWidth(88)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        params_layout.addWidget(self._stop_btn)

        self._export_btn = PushButton(FluentIcon.SAVE, "导出图片")
        self._export_btn.setFixedWidth(100)
        self._export_btn.clicked.connect(self._on_export)
        params_layout.addWidget(self._export_btn)

        root.addWidget(params_card)

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
        self._curve_distance = self._plot_widget.plot(
            [], [], pen=pg.mkPen(color="#a6e3a1", width=2), name="位移 %"
        )
        root.addWidget(self._plot_widget, stretch=1)

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(4, 0, 4, 0)
        status_layout.setSpacing(16)

        self._status_lbl = CaptionLabel("● 待机")
        self._status_lbl.setStyleSheet("color: #585b70;")
        status_layout.addWidget(self._status_lbl)

        self._cycle_lbl = CaptionLabel("周期: —")
        status_layout.addWidget(self._cycle_lbl)

        self._duration_lbl = CaptionLabel("时长: —")
        status_layout.addWidget(self._duration_lbl)

        self._current_val_lbl = CaptionLabel("电流: —")
        status_layout.addWidget(self._current_val_lbl)

        self._distance_val_lbl = CaptionLabel("位移: —")
        status_layout.addWidget(self._distance_val_lbl)

        status_layout.addStretch()
        root.addLayout(status_layout)

    def set_controller(self, controller: "MeasurementController") -> None:
        self._controller = controller
        controller.step_changed.connect(self._on_step_changed)
        controller.sample_ready.connect(self._on_sample_ready)
        controller.measurement_finished.connect(self._on_measurement_finished)

    def detach_controller(self) -> None:
        if self._controller is None:
            return
        try:
            self._controller.step_changed.disconnect(self._on_step_changed)
            self._controller.sample_ready.disconnect(self._on_sample_ready)
            self._controller.measurement_finished.disconnect(self._on_measurement_finished)
        except RuntimeError:
            pass
        self._controller = None

    def start(self, mode: str) -> None:
        if self._controller is None:
            return
        self._last_mode = mode
        self._apply_params_to_controller()
        self._reset_plot()
        self._set_running(True)
        self._controller.start(mode)

    @Slot()
    def _on_single(self) -> None:
        self.start("single")

    @Slot()
    def _on_auto(self) -> None:
        self.start("auto")

    @Slot()
    def _on_stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()

    @Slot()
    def _on_export(self) -> None:
        from pyqtgraph.exporters import ImageExporter

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出波形图",
            "measurement.png",
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg)",
        )
        if not path:
            return
        exporter = ImageExporter(self._plot_widget.plotItem)
        exporter.export(path)

    @Slot(int, float)
    def _on_step_changed(self, _step_index: int, current_pct: float) -> None:
        self._current_val_lbl.setText(f"电流: {current_pct:.0f}%")

    @Slot(float, float, float, float)
    def _on_sample_ready(self, elapsed_s: float, current_pct: float, distance_pct: float, distance_mm: float) -> None:
        self._time_data.append(elapsed_s)
        self._current_data.append(current_pct)
        self._distance_data.append(distance_pct)

        self._curve_current.setData(self._time_data, self._current_data)
        self._curve_distance.setData(self._time_data, self._distance_data)
        self._plot_widget.setXRange(0, max(1.0, elapsed_s), padding=0)

        mins = int(elapsed_s) // 60
        secs = int(elapsed_s) % 60
        self._duration_lbl.setText(f"时长: {mins:02d}:{secs:02d}")
        self._distance_val_lbl.setText(f"位移: {distance_pct:.1f}% ({distance_mm:.2f}mm)")

    @Slot(int, float)
    def _on_measurement_finished(self, cycle_count: int, _duration_s: float) -> None:
        self._set_running(False)
        if self._last_mode == "single":
            self._status_lbl.setText("● 单次完成")
        else:
            self._status_lbl.setText("● 自动已停止")
        self._status_lbl.setStyleSheet("color: #a6adc8;")
        self._cycle_lbl.setText(f"周期: {cycle_count}")

    def _apply_params_to_controller(self) -> None:
        if self._controller is None:
            return
        self._controller.step_period_s = self._period_spin.value()
        self._controller.sample_interval_ms = self._sample_spin.value()
        self._controller.displacement_peak_mm = self._peak_spin.value()

    def _reset_plot(self) -> None:
        self._time_data.clear()
        self._current_data.clear()
        self._distance_data.clear()
        self._curve_current.setData([], [])
        self._curve_distance.setData([], [])
        self._current_val_lbl.setText("电流: —")
        self._distance_val_lbl.setText("位移: —")
        self._cycle_lbl.setText("周期: 0")
        self._duration_lbl.setText("时长: 00:00")

    def _set_running(self, running: bool) -> None:
        self._period_spin.setEnabled(not running)
        self._sample_spin.setEnabled(not running)
        self._peak_spin.setEnabled(not running)
        self._single_btn.setEnabled(not running)
        self._auto_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if running:
            self._status_lbl.setText("● 测量中")
            self._status_lbl.setStyleSheet("color: #a6e3a1;")
