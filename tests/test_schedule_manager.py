from datetime import datetime
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schedule.manager import ScheduleManager, TimeWindow, _in_window



def _ensure_app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app



def test_in_window_treats_start_as_inclusive_and_end_as_exclusive() -> None:
    window = TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *")

    assert _in_window(window, datetime(2026, 4, 8, 9, 0, 0)) is True
    assert _in_window(window, datetime(2026, 4, 8, 16, 59, 59)) is True
    assert _in_window(window, datetime(2026, 4, 8, 17, 0, 0)) is False



def test_manager_defaults_to_active_when_disabled_or_without_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")

    assert manager.is_enabled() is False
    assert manager.is_active() is True
    assert manager.next_transition() is None



def test_manager_becomes_inactive_outside_enabled_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *")])
    manager.set_enabled(True)

    assert manager._compute_active(datetime(2026, 4, 8, 8, 0, 0)) is False
    assert manager._compute_active(datetime(2026, 4, 8, 9, 30, 0)) is True



def test_next_transition_reports_start_when_currently_inactive(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *")])
    manager.set_enabled(True)

    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 8, 0, 0))

    assert will_activate is True
    assert when == datetime(2026, 4, 8, 9, 0, 0)



def test_manager_persists_enabled_flag_and_windows(tmp_path: Path) -> None:
    _ensure_app()
    config_path = tmp_path / "schedule.json"
    manager = ScheduleManager(config_path)
    window = TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *", label="Workday")

    manager.set_windows([window])
    manager.set_enabled(True)

    reloaded = ScheduleManager(config_path)
    windows = reloaded.windows()

    assert reloaded.is_enabled() is True
    assert len(windows) == 1
    assert windows[0].start_cron == window.start_cron
    assert windows[0].end_cron == window.end_cron
    assert windows[0].label == "Workday"
    assert windows[0].id == window.id
