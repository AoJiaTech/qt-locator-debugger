# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A PySide6 desktop GUI for multi-port serial debugging of sensor devices. Features include: device management with per-device serial port configuration, protocol parsing, step-based measurement with real-time plotting, cron-based scheduled operation windows, and session history with resume capability.

## Commands

```bash
# Package management (use uv, not pip)
uv sync --all-extras --dev
uv add <package>

# Run the application
uv run python main.py

# Run tests
uv run pytest
uv run pytest tests/schedule/test_manager.py          # single test file
uv run pytest -k "test_in_window"                      # single test by name

# Database migrations
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head

# Linting
uv run ruff check .
uv run ruff check --fix .

# Build (Nuitka, done via CI)
# Local: uv run nuitka --onefile --enable-plugin=pyside6 main.py
```

## Architecture

### Event Loop

`main.py` bridges Qt and asyncio via `QAsyncioEventLoop` — all async operations (serial I/O, DB access) run within the Qt event loop. Use `asyncio.create_task()` for fire-and-forget coroutines from Qt slots.

### Data Flow

```
Serial Hardware → SerialWorker._read_loop() → parser.parse(chunk) → Frame
    ├── frame_received signal → UI display
    └── repository.save() → SQLite

UI button → asyncio.create_task(worker.send(data)) → Serial → frame_received(TX echo)
```

### Key Components

- **`app/models/domain.py`** — Plain dataclasses: `Frame`, `PortConfig`, `DeviceConfig`, `MeasurementState`
- **`app/models/db.py`** — SQLAlchemy ORM: `DeviceRecord`, `ParsedRecord`, `MeasurementSession`, `MeasurementPoint`
- **`app/serial/worker.py`** — `SerialWorker`: single-port async read/write, emits `frame_received` signal, supports one-shot and loop-send modes
- **`app/serial/manager.py`** — `SerialManager`: dict-based worker registry keyed by `device_id`
- **`app/serial/parser.py`** — `BaseParser` ABC + `BUILTIN_PARSERS` registry dict; parsers must handle stream reassembly internally (no frame boundary guarantees from `reader.read()`)
- **`app/measurement/controller.py`** — `MeasurementController`: drives step sequences (write register → wait echo → sample distance → repeat), emits `sample_ready` for live plotting
- **`app/schedule/manager.py`** — `ScheduleManager`: cron-based time windows stored in JSON config, evaluates active/inactive state every 30s via QTimer
- **`app/storage/repository.py`** — `BaseRepository` ABC → `SQLAlchemyRepository` (async SQLite via aiosqlite)

### UI Layer (`app/ui/`)

FluentWindow with three sub-interfaces:
- **调试主页** — Left: `DeviceListPanel` (device cards), Right: `DevicePanel` (tabbed per-device detail with send/receive + measurement controls)
- **定时运行** — `SchedulePage` for cron window configuration
- **历史记录** — `HistoryPage` with session list and resume capability

### Adding a New Device Type

1. Create parser in `app/serial/parsers/` extending `BaseParser` (handle stream buffering internally)
2. Register in `BUILTIN_PARSERS` dict in `app/serial/parser.py` (laser parser is registered in `main_window.py`)
3. Optionally subclass `DeviceCard` in `app/ui/device_list_panel.py` for device-specific action buttons
4. See `docs/device-integration-guide.md` for full walkthrough

### Persistence

- SQLite database: `data.db` (auto-created, migrations run on startup via `repo.init_db()`)
- Alembic migrations in `migrations/versions/`
- Device configs, parsed frames, measurement sessions + points are all persisted
- Schedule config stored separately in `schedule_config.json`

## Conventions

- Python 3.14, line length 120
- Linting: ruff (rules: E, W, F, UP, C, T, PYI, PT, Q), ignore E402 and C901
- Import sorting: isort with black profile
- Type checking: pyright in standard mode
- All async DB access uses SQLAlchemy async sessions with aiosqlite
- Serial commands use Modbus RTU framing (see `app/utils.py` for CRC helpers)
