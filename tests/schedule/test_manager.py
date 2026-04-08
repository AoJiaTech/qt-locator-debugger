import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from app.schedule import manager as schedule_manager
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



def test_in_window_supports_overnight_windows() -> None:
    window = TimeWindow(start_cron="0 22 * * *", end_cron="0 6 * * *")

    assert _in_window(window, datetime(2026, 4, 8, 21, 59, 59)) is False
    assert _in_window(window, datetime(2026, 4, 8, 22, 0, 0)) is True
    assert _in_window(window, datetime(2026, 4, 9, 1, 30, 0)) is True
    assert _in_window(window, datetime(2026, 4, 9, 6, 0, 0)) is False



def test_manager_defaults_to_active_when_disabled_or_without_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")

    assert manager.is_enabled() is False
    assert manager.is_active() is True
    assert manager.next_transition() is None



def test_manager_treats_enabled_without_windows_as_active(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_enabled(True)

    assert manager.is_active() is True
    assert manager._compute_active(datetime(2026, 4, 8, 8, 0, 0)) is True
    assert manager.next_transition(datetime(2026, 4, 8, 8, 0, 0)) is None



def test_manager_becomes_inactive_outside_enabled_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *")])
    manager.set_enabled(True)

    assert manager._compute_active(datetime(2026, 4, 8, 8, 0, 0)) is False
    assert manager._compute_active(datetime(2026, 4, 8, 9, 30, 0)) is True



def test_manager_supports_two_work_windows_with_lunch_break(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows(
        [
            TimeWindow(start_cron="0 9 * * *", end_cron="0 12 * * *", label="Morning"),
            TimeWindow(start_cron="0 13 * * *", end_cron="0 18 * * *", label="Afternoon"),
        ]
    )
    manager.set_enabled(True)

    assert manager._compute_active(datetime(2026, 4, 8, 10, 0, 0)) is True
    assert manager._compute_active(datetime(2026, 4, 8, 12, 30, 0)) is False
    assert manager._compute_active(datetime(2026, 4, 8, 14, 0, 0)) is True



def test_compute_active_ignores_disabled_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows(
        [
            TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *", label="Disabled", enabled=False),
            TimeWindow(start_cron="0 10 * * *", end_cron="0 11 * * *", label="Enabled"),
        ]
    )
    manager.set_enabled(True)

    assert manager._compute_active(datetime(2026, 4, 8, 9, 30, 0)) is False
    assert manager._compute_active(datetime(2026, 4, 8, 10, 30, 0)) is True



def test_manager_supports_overnight_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 22 * * *", end_cron="0 6 * * *", label="Night")])
    manager.set_enabled(True)

    assert manager._compute_active(datetime(2026, 4, 8, 21, 0, 0)) is False
    assert manager._compute_active(datetime(2026, 4, 8, 23, 0, 0)) is True
    assert manager._compute_active(datetime(2026, 4, 9, 5, 30, 0)) is True
    assert manager._compute_active(datetime(2026, 4, 9, 7, 0, 0)) is False



def test_next_transition_reports_start_when_currently_inactive(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *")])
    manager.set_enabled(True)

    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 8, 0, 0))

    assert will_activate is True
    assert when == datetime(2026, 4, 8, 9, 0, 0)



def test_next_transition_reports_nearest_end_when_currently_active(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows(
        [
            TimeWindow(start_cron="0 9 * * *", end_cron="0 12 * * *", label="Morning"),
            TimeWindow(start_cron="0 13 * * *", end_cron="0 18 * * *", label="Afternoon"),
        ]
    )
    manager.set_enabled(True)

    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 10, 0, 0))

    assert will_activate is False
    assert when == datetime(2026, 4, 8, 12, 0, 0)



def test_next_transition_returns_aggregate_end_for_overlapping_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows(
        [
            TimeWindow(start_cron="0 9 * * *", end_cron="0 12 * * *", label="A"),
            TimeWindow(start_cron="0 10 * * *", end_cron="0 15 * * *", label="B"),
        ]
    )
    manager.set_enabled(True)

    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 10, 30, 0))

    assert will_activate is False
    assert when == datetime(2026, 4, 8, 15, 0, 0)



def test_next_transition_handles_overnight_windows(tmp_path: Path) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    manager.set_windows([TimeWindow(start_cron="0 22 * * *", end_cron="0 6 * * *", label="Night")])
    manager.set_enabled(True)

    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 23, 0, 0))

    assert will_activate is False
    assert when == datetime(2026, 4, 9, 6, 0, 0)



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



def test_load_skips_malformed_window_entries(tmp_path: Path, monkeypatch) -> None:
    _ensure_app()
    config_path = tmp_path / "schedule.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "windows": [
                    {"start_cron": "0 9 * * *", "end_cron": "0 17 * * *", "label": "Workday"},
                    "not-a-dict",
                    {"start_cron": "0 12 * * *"},
                    {"start_cron": "0 18 * * *", "end_cron": "0 20 * * *", "extra": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    warnings: list[str] = []
    monkeypatch.setattr(schedule_manager.logger, "warning", warnings.append)

    manager = ScheduleManager(config_path)
    windows = manager.windows()

    assert manager.is_enabled() is True
    assert len(windows) == 1
    assert windows[0].label == "Workday"
    assert any("跳过无效时间窗配置" in message for message in warnings)



def test_in_window_returns_false_for_invalid_cron(monkeypatch) -> None:
    window = TimeWindow(start_cron="invalid cron", end_cron="0 17 * * *")
    warnings: list[str] = []

    monkeypatch.setattr(schedule_manager.logger, "warning", warnings.append)

    assert _in_window(window, datetime(2026, 4, 8, 9, 0, 0)) is False
    assert any("invalid time window" in message for message in warnings)



def test_next_transition_skips_invalid_windows(tmp_path: Path, monkeypatch) -> None:
    _ensure_app()
    manager = ScheduleManager(tmp_path / "schedule.json")
    warnings: list[str] = []
    monkeypatch.setattr(schedule_manager.logger, "warning", warnings.append)
    manager.set_windows(
        [
            TimeWindow(start_cron="invalid cron", end_cron="0 17 * * *", label="Broken"),
            TimeWindow(start_cron="0 9 * * *", end_cron="0 17 * * *", label="Workday"),
        ]
    )
    manager.set_enabled(True)

    warnings.clear()
    will_activate, when = manager.next_transition(datetime(2026, 4, 8, 8, 0, 0))

    assert will_activate is True
    assert when == datetime(2026, 4, 8, 9, 0, 0)
    assert any("invalid time window" in message for message in warnings)
