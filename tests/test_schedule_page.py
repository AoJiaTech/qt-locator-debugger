from __future__ import annotations

from PySide6.QtWidgets import QWidget, QApplication

from app.ui.schedule_page import TimeWindowDialog


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    return app


def _create_dialog() -> TimeWindowDialog:
    _ensure_app()
    parent = QWidget()
    parent.resize(800, 600)
    dialog = TimeWindowDialog(parent=parent)
    dialog._test_parent = parent
    return dialog


def test_invalid_simple_input_does_not_accept_dialog() -> None:
    dialog = _create_dialog()
    for checkbox in dialog._weekday_checks.values():
        checkbox.setChecked(False)

    dialog.yesButton.click()

    assert dialog.result() == 0
    assert dialog.get_result() is None


def test_simple_mode_rejects_identical_start_and_end_crons() -> None:
    dialog = _create_dialog()
    dialog._start_time_edit.setTime(dialog._end_time_edit.time())

    dialog.yesButton.click()

    assert dialog.result() == 0
    assert dialog.get_result() is None


def test_advanced_mode_rejects_identical_start_and_end_crons() -> None:
    dialog = _create_dialog()
    dialog._mode_switch.setChecked(False)
    dialog._start_cron_edit.setText("0 9 * * 1-5")
    dialog._end_cron_edit.setText("0 9 * * 1-5")

    dialog.yesButton.click()

    assert dialog.result() == 0
    assert dialog.get_result() is None
