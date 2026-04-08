from __future__ import annotations

from PySide6.QtWidgets import QApplication

from app.ui.measurement_panel import MeasurementPanel


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self._callbacks.remove(callback)


class _ControllerStub:
    def __init__(self) -> None:
        self.step_changed = _Signal()
        self.sample_ready = _Signal()
        self.measurement_finished = _Signal()
        self.measurement_paused = _Signal()
        self._running = False
        self.pause_calls = 0
        self.stop_calls = 0
        self.start_calls: list[tuple[str, float | None]] = []
        self.resume_calls: list[dict[str, object]] = []
        self._session_id = 42
        self._current_step = 3
        self._cycle_count = 5
        self._baseline_distance_mm = 1.25
        self.step_period_s = 0.0
        self.sample_interval_ms = 0
        self.displacement_peak_mm = 0.0

    def start(self, mode: str, baseline_mm: float | None = None) -> None:
        self._running = True
        self.start_calls.append((mode, baseline_mm))

    def resume(self, **kwargs) -> None:
        self._running = True
        self.resume_calls.append(kwargs)

    def pause(self) -> None:
        self.pause_calls += 1
        self._running = False

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False

    def is_running(self) -> bool:
        return self._running


class _ScheduleManagerStub:
    def __init__(self, active: bool) -> None:
        self._active = active
        self.active_changed = _Signal()

    def is_active(self) -> bool:
        return self._active


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    return app


def _create_panel() -> tuple[MeasurementPanel, _ControllerStub]:
    _ensure_app()
    panel = MeasurementPanel()
    controller = _ControllerStub()
    panel.set_controller(controller)
    return panel, controller


def test_start_clears_stale_schedule_pause_state() -> None:
    panel, controller = _create_panel()
    panel._paused_by_schedule = True

    panel.start("single")

    assert panel._paused_by_schedule is False
    assert controller.start_calls == [("single", None)]


def test_start_from_session_clears_stale_schedule_pause_state() -> None:
    panel, controller = _create_panel()
    panel._paused_by_schedule = True

    panel.start_from_session(
        session_id=7,
        step_index=1,
        cycle_count=2,
        time_offset=0.5,
        history_time=[0.1],
        history_current=[10.0],
        history_distance=[20.0],
        step_period_s=2.0,
        sample_interval_ms=200,
        displacement_peak_mm=50.0,
    )

    assert panel._paused_by_schedule is False
    assert controller.resume_calls[0]["session_id"] == 7


def test_stop_clears_stale_schedule_pause_state() -> None:
    panel, controller = _create_panel()
    panel._paused_by_schedule = True

    panel._on_stop()

    assert panel._paused_by_schedule is False
    assert controller.stop_calls == 1


def test_measurement_finished_clears_stale_schedule_pause_state() -> None:
    panel, _controller = _create_panel()
    panel._paused_by_schedule = True

    panel._on_measurement_finished(cycle_count=3, duration_s=8.0)

    assert panel._paused_by_schedule is False


def test_detach_controller_clears_stale_schedule_pause_state() -> None:
    panel, _controller = _create_panel()
    panel._paused_by_schedule = True

    panel.detach_controller()

    assert panel._paused_by_schedule is False
    assert panel._controller is None


def test_schedule_manager_syncs_immediately_when_attached_in_inactive_state() -> None:
    panel, controller = _create_panel()
    panel.start("auto")
    manager = _ScheduleManagerStub(active=False)

    panel.set_schedule_manager(manager)

    assert panel._paused_by_schedule is True
    assert controller.pause_calls == 1
    assert panel._status_lbl.text() == "● 已按计划暂停"


def test_schedule_activation_does_not_resume_after_finished_session() -> None:
    panel, controller = _create_panel()
    panel.start("auto")
    panel._on_schedule_changed(False)
    panel._on_measurement_finished(cycle_count=4, duration_s=12.0)

    panel._on_schedule_changed(True)

    assert panel._paused_by_schedule is False
    assert controller.resume_calls == []
