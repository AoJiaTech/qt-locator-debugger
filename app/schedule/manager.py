import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from croniter import croniter
from PySide6.QtCore import QObject, QTimer, Signal

from app.logger import logger


@dataclass(slots=True)
class TimeWindow:
    start_cron: str
    end_cron: str
    label: str = ""
    enabled: bool = True
    id: str = field(default_factory=lambda: str(uuid4()))


_DEFAULT_CONFIG = {"enabled": False, "windows": []}


def _in_window(window: TimeWindow, now: datetime) -> bool:
    probe = now + timedelta(microseconds=1)
    previous_start = croniter(window.start_cron, probe).get_prev(datetime)
    previous_end = croniter(window.end_cron, probe).get_prev(datetime)
    return previous_start >= previous_end


class ScheduleManager(QObject):
    active_changed = Signal(bool)

    def __init__(self, config_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config_path = Path(config_path)
        self._enabled = False
        self._windows: list[TimeWindow] = []
        self._active = True

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self._evaluate)

        self._load()
        self._timer.start()
        self._evaluate()

    def is_active(self) -> bool:
        return self._active

    def is_enabled(self) -> bool:
        return self._enabled

    def windows(self) -> list[TimeWindow]:
        return [TimeWindow(**asdict(window)) for window in self._windows]

    def next_transition(self, now: datetime | None = None) -> tuple[bool, datetime] | None:
        if not self._enabled:
            return None

        enabled_windows = [window for window in self._windows if window.enabled]
        if not enabled_windows:
            return None

        current = now or datetime.now()
        active = self._compute_active(current)
        candidates: list[tuple[bool, datetime]] = []
        for window in enabled_windows:
            start_at = croniter(window.start_cron, current).get_next(datetime)
            end_at = croniter(window.end_cron, current).get_next(datetime)
            candidates.append((True, start_at))
            candidates.append((False, end_at))

        target_state = not active
        matching = [candidate for candidate in candidates if candidate[0] is target_state]
        if not matching:
            return None
        return min(matching, key=lambda item: item[1])

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._enabled == enabled:
            return
        self._enabled = enabled
        self._save()
        self._evaluate()

    def set_windows(self, windows: list[TimeWindow]) -> None:
        self._windows = [TimeWindow(**asdict(window)) for window in windows]
        self._save()
        self._evaluate()

    def _compute_active(self, now: datetime | None = None) -> bool:
        if not self._enabled:
            return True

        enabled_windows = [window for window in self._windows if window.enabled]
        if not enabled_windows:
            return True

        current = now or datetime.now()
        return any(_in_window(window, current) for window in enabled_windows)

    def _evaluate(self) -> None:
        next_active = self._compute_active()
        if next_active == self._active:
            return
        self._active = next_active
        logger.info(f"[ScheduleManager] active={self._active}")
        self.active_changed.emit(self._active)

    def _load(self) -> None:
        if not self._config_path.exists():
            return

        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"[ScheduleManager] 加载配置失败: {exc}")
            return

        self._enabled = bool(data.get("enabled", False))
        windows_data = data.get("windows", [])
        windows: list[TimeWindow] = []
        if isinstance(windows_data, list):
            for item in windows_data:
                if not isinstance(item, dict):
                    continue
                try:
                    windows.append(TimeWindow(**item))
                except TypeError as exc:
                    logger.warning(f"[ScheduleManager] 跳过无效时间窗配置: {exc}")
        self._windows = windows

    def _save(self) -> None:
        payload = {
            "enabled": self._enabled,
            "windows": [asdict(window) for window in self._windows],
        }
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(f"[ScheduleManager] 保存配置失败: {exc}")
