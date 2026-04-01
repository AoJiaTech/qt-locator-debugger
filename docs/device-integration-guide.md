# 设备接入开发指南

本文档面向在此框架上接入具体传感器设备的开发者，介绍如何实现协议解析、持久化存储扩展，以及如何使用串口发送接口。

---

## 目录

1. [整体数据流](#1-整体数据流)
2. [实现自定义协议解析器](#2-实现自定义协议解析器)
3. [数据库模型与持久化](#3-数据库模型与持久化)
4. [串口发送接口](#4-串口发送接口)
5. [在 DeviceCard 中添加设备专属操作](#5-在-devicecard-中添加设备专属操作)
6. [完整接入示例](#6-完整接入示例)

---

## 1. 整体数据流

```
串口硬件
   │  原始字节（bytes）
   ▼
SerialWorker._read_loop()
   │  调用 parser.parse(chunk) → dict
   │  封装为 Frame(direction=RX, raw=..., parsed=...)
   ├──► frame_received 信号 ──► UI（PortTab 接收区显示）
   └──► repository.save(device_id, frame) ──► SQLite 数据库
```

发送方向：

```
UI 按钮 / DeviceCard 专属按钮
   │  asyncio.create_task(worker.send(data))
   ▼
SerialWorker.send()
   │  写入串口
   └──► frame_received 信号（TX 回显）──► UI
```

---

## 2. 实现自定义协议解析器

### 2.1 基类接口

文件：[`app/serial/parser.py`](../app/serial/parser.py)

```python
class BaseParser(ABC):
    @abstractmethod
    def parse(self, data: bytes) -> dict:
        """
        将原始字节解析为结构化字典。
        返回的字典会：
          1. 在 UI 接收区追加显示（"解析: {...}"）
          2. 序列化为 JSON 写入数据库 parsed_json 字段
        """
        ...

    def to_record(self, frame: Frame) -> dict | None:
        """
        可选钩子：返回用于持久化的标准化字典。
        默认返回 None，框架直接存储 parse() 的返回值。
        若需要自定义存储结构（如拆字段），在此覆盖。
        """
        return None
```

### 2.2 注意事项

- `parse()` 接收的 `data` 是**单次 `reader.read(1024)` 的原始字节**，不保证完整帧边界。  
  如果你的协议有帧头/帧尾，需要在解析器内部维护**粘包缓冲区**（见 2.4）。
- `parse()` 必须始终返回 `dict`，解析失败时返回 `{"error": "..."}` 而不是抛异常。
- 返回值中的键名可以自由定义，UI 会原样显示。

### 2.3 简单示例：固定长度帧

假设设备每帧 8 字节：`[0xAA][0xBB][len][d0][d1][d2][crc_lo][crc_hi]`

```python
# app/serial/parsers/my_sensor.py

import struct
from app.serial.parser import BaseParser


class MySensorParser(BaseParser):

    def parse(self, data: bytes) -> dict:
        if len(data) < 8:
            return {"error": f"帧长不足: {len(data)} bytes"}
        if data[0] != 0xAA or data[1] != 0xBB:
            return {"error": f"帧头错误: {data[:2].hex()}"}

        length = data[2]
        d0, d1, d2 = data[3], data[4], data[5]
        crc = struct.unpack_from("<H", data, 6)[0]

        return {
            "length": length,
            "channel_0": d0,
            "channel_1": d1,
            "channel_2": d2,
            "crc": f"0x{crc:04X}",
        }
```

### 2.4 进阶示例：带粘包处理的流式解析

若数据到达不保证对齐，需维护内部缓冲区：

```python
class StreamParser(BaseParser):

    HEADER = b'\xAA\xBB'
    FRAME_LEN = 8

    def __init__(self):
        self._buf = bytearray()

    def parse(self, data: bytes) -> dict:
        self._buf.extend(data)
        results = []

        while len(self._buf) >= self.FRAME_LEN:
            # 搜索帧头
            idx = self._buf.find(self.HEADER)
            if idx == -1:
                self._buf.clear()
                break
            if idx > 0:
                # 丢弃帧头之前的脏数据
                del self._buf[:idx]

            if len(self._buf) < self.FRAME_LEN:
                break  # 等待更多数据

            frame_bytes = bytes(self._buf[:self.FRAME_LEN])
            del self._buf[:self.FRAME_LEN]

            # 解析单帧
            d0, d1, d2 = frame_bytes[3], frame_bytes[4], frame_bytes[5]
            results.append({"ch0": d0, "ch1": d1, "ch2": d2})

        if not results:
            return {"status": "buffering"}
        if len(results) == 1:
            return results[0]
        return {"frames": results}
```

### 2.5 注册到 UI 下拉菜单

在 [`app/serial/parser.py`](../app/serial/parser.py) 末尾的注册表中添加：

```python
from app.serial.parsers.my_sensor import MySensorParser

BUILTIN_PARSERS: dict[str, type[BaseParser]] = {
    "Raw Hex":    HexParser,
    "ASCII":      AsciiParser,
    "我的传感器": MySensorParser,   # ← 添加这一行
}
```

UI 串口配置卡片的「解析」下拉框会自动出现此选项。

---

## 3. 数据库模型与持久化

### 3.1 现有表结构

文件：[`app/models/db.py`](../app/models/db.py)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `device_id` | VARCHAR(64) | 设备标识，如 `"device_1"` |
| `port` | VARCHAR(32) | 串口号，如 `"COM3"` |
| `direction` | VARCHAR(4) | `"TX"` 或 `"RX"` |
| `raw_hex` | TEXT | 原始字节十六进制，如 `"AA BB CC"` |
| `parsed_json` | TEXT (nullable) | `parse()` 返回值的 JSON 序列化 |
| `timestamp` | DATETIME | 帧到达时间（本地时间） |

`device_id` 和 `timestamp` 建有索引，适合按设备、时间范围查询。

### 3.2 扩展：添加设备专属表

若需要将解析字段拆分为独立列（方便后续 SQL 聚合和绘图），用 alembic 新建迁移：

**Step 1**：在 `app/models/db.py` 添加新模型

```python
class MySensorRecord(Base):
    """我的传感器专属解析记录表。"""

    __tablename__ = "my_sensor_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    channel_0: Mapped[int] = mapped_column()
    channel_1: Mapped[int] = mapped_column()
    channel_2: Mapped[int] = mapped_column()
    crc: Mapped[str] = mapped_column(String(8))
```

**Step 2**：生成迁移脚本（项目根目录执行）

```bash
uv run alembic revision --autogenerate -m "add my_sensor_records"
uv run alembic upgrade head
```

**Step 3**：实现专属 Repository（可选，也可直接复用通用的 `SQLAlchemyRepository`）

```python
# app/storage/my_sensor_repository.py

import json
from app.models.db import MySensorRecord
from app.models.domain import Frame
from app.storage.repository import SQLAlchemyRepository


class MySensorRepository(SQLAlchemyRepository):

    async def save(self, device_id: str, frame: Frame) -> None:
        # 先调用父类存通用记录
        await super().save(device_id, frame)

        # 再存专属表
        if not frame.parsed or "error" in frame.parsed:
            return

        record = MySensorRecord(
            device_id=device_id,
            timestamp=frame.timestamp,
            channel_0=frame.parsed["channel_0"],
            channel_1=frame.parsed["channel_1"],
            channel_2=frame.parsed["channel_2"],
            crc=frame.parsed["crc"],
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
```

### 3.3 查询示例

```python
from sqlalchemy import select
from app.models.db import MySensorRecord

async def query_last_n(session, device_id: str, n: int = 100):
    stmt = (
        select(MySensorRecord)
        .where(MySensorRecord.device_id == device_id)
        .order_by(MySensorRecord.timestamp.desc())
        .limit(n)
    )
    result = await session.execute(stmt)
    return result.scalars().all()
```

---

## 4. 串口发送接口

发送操作通过 `SerialWorker` 实例完成，始终在 asyncio 协程中调用。

文件：[`app/serial/worker.py`](../app/serial/worker.py)

### 4.1 单次发送

```python
async def send(self, data: bytes) -> None
```

**用法：**

```python
# 从设备卡片的专属按钮槽函数中调用
import asyncio

@Slot()
def _on_query_btn_clicked(self) -> None:
    if self.worker is None:
        return
    # 构造查询命令帧（示例：AA BB 01 00 CC）
    cmd = bytes([0xAA, 0xBB, 0x01, 0x00, 0xCC])
    asyncio.create_task(self.worker.send(cmd))
```

发送后会自动 emit `frame_received` 信号（`direction=TX`），UI 接收区会同步回显。

### 4.2 持续发送（循环发送至事件停止）

```python
async def start_loop_send(self, data: bytes, interval_ms: int) -> None
async def stop_loop_send(self) -> None
```

**行为说明：**

- `start_loop_send()` 启动一个独立 asyncio Task，每隔 `interval_ms` 毫秒发送一次 `data`。
- 内部使用 `asyncio.Event`（`_loop_stop_event`）作为停止信号。
- 如果循环任务已在运行，重复调用 `start_loop_send()` 不会重复启动。
- `stop_loop_send()` 设置停止事件并等待任务退出（干净退出，不强行 cancel）。

**用法示例——收到特定响应后自动停止：**

```python
# app/serial/parsers/my_sensor.py 中

class MySensorParser(BaseParser):
    ...

# app/ui/my_device_card.py 中

class MyDeviceCard(DeviceCard):

    def _build_device_actions(self) -> None:
        self._sep.show()

        self._poll_btn = PushButton(FluentIcon.PLAY, "开始轮询")
        self._poll_btn.clicked.connect(self._on_poll_start)
        self._actions_layout.addWidget(self._poll_btn)

    @Slot()
    def _on_poll_start(self) -> None:
        if self.worker is None:
            return
        cmd = bytes([0xAA, 0xBB, 0x01])
        asyncio.create_task(self.worker.start_loop_send(cmd, interval_ms=500))
        # 监听接收信号，收到特定响应时停止
        self.worker.frame_received.connect(self._on_frame_check)

    @Slot(Frame)
    def _on_frame_check(self, frame: Frame) -> None:
        if frame.direction != Direction.RX:
            return
        # 假设收到 0xFF 0x00 表示设备就绪，停止轮询
        if frame.raw[:2] == b'\xFF\x00':
            asyncio.create_task(self.worker.stop_loop_send())
            self.worker.frame_received.disconnect(self._on_frame_check)
```

**关键点：**

| 场景 | 做法 |
|------|------|
| 按钮触发停止 | 调用 `asyncio.create_task(worker.stop_loop_send())` |
| 收到特定帧停止 | 在 `frame_received` 槽中判断，满足条件则调用上述方法 |
| 超时自动停止 | 配合 `asyncio.create_task(asyncio.sleep(n))` 后调用 stop |
| 串口断开时 | Worker 内部自动停止，无需手动处理 |

---

## 5. 在 DeviceCard 中添加设备专属操作

子类化 `DeviceCard`，覆盖 `_build_device_actions()` 方法。

文件：[`app/ui/device_list_panel.py`](../app/ui/device_list_panel.py)

```python
# app/ui/my_device_card.py

import asyncio
from PySide6.QtCore import Slot
from qfluentwidgets import PushButton, FluentIcon
from app.ui.device_list_panel import DeviceCard


class MyDeviceCard(DeviceCard):
    """传感器 A 的专属卡片，增加"查询状态"和"校零"按钮。"""

    def _build_device_actions(self) -> None:
        self._sep.show()   # 显示分隔线

        self._query_btn = PushButton(FluentIcon.SEARCH, "查询状态")
        self._query_btn.clicked.connect(self._on_query)
        self._actions_layout.addWidget(self._query_btn)

        self._calibrate_btn = PushButton(FluentIcon.SYNC, "校零")
        self._calibrate_btn.clicked.connect(self._on_calibrate)
        self._actions_layout.addWidget(self._calibrate_btn)

    @Slot()
    def _on_query(self) -> None:
        if self.worker:
            asyncio.create_task(self.worker.send(b'\xAA\x01\xFF'))

    @Slot()
    def _on_calibrate(self) -> None:
        if self.worker:
            asyncio.create_task(self.worker.send(b'\xAA\x02\xFF'))
```

在 `main_window.py` 的 `DEFAULT_DEVICES` 和 `DeviceListPanel` 中替换成你的卡片类：

```python
# app/ui/main_window.py（_build_ui 内）

from app.ui.my_device_card import MyDeviceCard

# DeviceListPanel 目前统一使用 DeviceCard，
# 若要替换，可以直接在 DeviceListPanel._build_ui() 中
# 根据 device_id 实例化不同的 Card 子类：

for cfg in device_configs:
    if cfg.device_id == "device_1":
        card = MyDeviceCard(cfg, manager, repository)
    else:
        card = DeviceCard(cfg, manager, repository)
    ...
```

---

## 6. 完整接入示例

以下以"三轴加速度传感器"为例，走完全流程。

### 协议定义

| 字节 | 含义 |
|------|------|
| 0 | 帧头 `0xAA` |
| 1 | 命令 `0x01`=采样数据 |
| 2–3 | X 轴（int16, little-endian） |
| 4–5 | Y 轴（int16, little-endian） |
| 6–7 | Z 轴（int16, little-endian） |
| 8 | 校验和（字节 1–7 之和的低 8 位） |

### Step 1：解析器

```python
# app/serial/parsers/accel.py

import struct
from app.serial.parser import BaseParser


class AccelParser(BaseParser):

    FRAME_LEN = 9

    def __init__(self):
        self._buf = bytearray()

    def parse(self, data: bytes) -> dict:
        self._buf.extend(data)

        while len(self._buf) >= self.FRAME_LEN:
            idx = self._buf.find(b'\xAA')
            if idx == -1:
                self._buf.clear()
                return {"status": "no header"}
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < self.FRAME_LEN:
                break

            frame = bytes(self._buf[:self.FRAME_LEN])
            del self._buf[:self.FRAME_LEN]

            checksum = sum(frame[1:8]) & 0xFF
            if checksum != frame[8]:
                return {"error": f"校验失败 got={frame[8]:02X} expect={checksum:02X}"}

            x, y, z = struct.unpack_from("<hhh", frame, 2)
            return {"x": x, "y": y, "z": z}

        return {"status": "buffering"}
```

### Step 2：注册

```python
# app/serial/parser.py
from app.serial.parsers.accel import AccelParser

BUILTIN_PARSERS = {
    "Raw Hex":   HexParser,
    "ASCII":     AsciiParser,
    "加速度传感器": AccelParser,
}
```

### Step 3：数据库扩展（可选）

```python
# app/models/db.py 追加
class AccelRecord(Base):
    __tablename__ = "accel_records"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    x: Mapped[int] = mapped_column()
    y: Mapped[int] = mapped_column()
    z: Mapped[int] = mapped_column()
```

### Step 4：专属卡片（持续采样 + 停止）

```python
# app/ui/accel_card.py

import asyncio
from PySide6.QtCore import Slot
from qfluentwidgets import PushButton, FluentIcon
from app.models.domain import Direction, Frame
from app.ui.device_list_panel import DeviceCard


class AccelCard(DeviceCard):

    def _build_device_actions(self) -> None:
        self._sep.show()

        self._start_btn = PushButton(FluentIcon.PLAY, "开始采样")
        self._start_btn.clicked.connect(self._on_start)
        self._actions_layout.addWidget(self._start_btn)

        self._stop_btn = PushButton(FluentIcon.PAUSE, "停止采样")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        self._actions_layout.addWidget(self._stop_btn)

    @Slot()
    def _on_start(self) -> None:
        if not self.worker:
            return
        # 发送"开始采样"命令，设备会持续上报
        asyncio.create_task(self.worker.send(b'\xAA\x10\xFF'))
        # 同时以 100ms 间隔循环轮询（可选，取决于设备协议）
        # asyncio.create_task(self.worker.start_loop_send(b'\xAA\x01\xFF', 100))
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    @Slot()
    def _on_stop(self) -> None:
        if not self.worker:
            return
        asyncio.create_task(self.worker.send(b'\xAA\x11\xFF'))  # 停止命令
        asyncio.create_task(self.worker.stop_loop_send())        # 停止轮询
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
```

---

## 附：关键文件索引

| 文件 | 职责 |
|------|------|
| [`app/serial/parser.py`](../app/serial/parser.py) | `BaseParser` 基类 + 内置解析器 + 注册表 |
| [`app/serial/worker.py`](../app/serial/worker.py) | 串口异步读写、单次/循环发送 |
| [`app/serial/manager.py`](../app/serial/manager.py) | 多设备 Worker 生命周期管理 |
| [`app/models/domain.py`](../app/models/domain.py) | `Frame`、`PortConfig`、`DeviceConfig` 数据类 |
| [`app/models/db.py`](../app/models/db.py) | SQLAlchemy ORM 模型 |
| [`app/storage/repository.py`](../app/storage/repository.py) | `BaseRepository` 抽象 + SQLite 实现 |
| [`app/ui/device_list_panel.py`](../app/ui/device_list_panel.py) | `DeviceCard` 基类（可子类化添加专属按钮） |
