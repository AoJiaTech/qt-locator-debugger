# 设备卡片增强功能设计文档

**日期**: 2026-04-02  
**状态**: 已批准  

---

## 背景与动机

当前设备卡片存在以下问题：

1. **串口配置不持久化**：用户每次启动应用都需要重新选择串口和波特率，`DeviceConfig.port_config` 在领域模型中已完整定义，但 `devices` 数据库表缺少对应字段，`repository` 层也未保存/恢复这些数据。

2. **设备名称不可修改**：标题使用只读 `StrongBodyLabel`，无编辑入口，但数据库和仓储层已支持保存 `name` 字段。

3. **卡片无法拖拽排序**：`devices` 表已有 `sort_order` 字段，但左侧卡片列表没有拖拽交互，无法让用户自定义顺序。

4. **高级串口参数无配置入口**：`bytesize`/`parity`/`stopbits` 在 `PortConfig` 中已定义，但界面上没有配置方式。

5. **小 bug**：`repository.save()` 中 `ParsedRecord.port` 被写死为空字符串，应保存实际端口名。

---

## 功能设计

### F1：串口配置持久化

#### 数据库层
在 `DeviceRecord`（`app/models/db.py`）新增五个可空字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `port` | VARCHAR(32) nullable | 串口名，如 `COM3` |
| `baudrate` | INTEGER nullable | 波特率，如 `9600` |
| `bytesize` | INTEGER nullable | 数据位，默认 8 |
| `parity` | VARCHAR(4) nullable | 校验位，默认 `"N"` |
| `stopbits` | FLOAT nullable | 停止位，默认 1.0 |

新增 Alembic 迁移文件 `migrations/versions/xxxx_add_port_config_to_devices.py`，在 `upgrade()` 中用 `op.add_column()` 添加以上字段，`downgrade()` 中删除。

#### 仓储层（`app/storage/repository.py`）

**`save_device(cfg, read_cmd_hex, sort_order)`**：
- 新增 `port_config: PortConfig | None = None` 参数
- 若 `port_config` 不为 None，写入五个字段；若为 None，写入 NULL

**`load_devices()`**：
- 读取时，若 `port` 字段不为 None，构造 `PortConfig` 对象赋给 `cfg.port_config`
- 否则 `cfg.port_config = None`

#### UI 层（`app/ui/device_list_panel.py`）

- 串口下拉框 `currentIndexChanged` 和波特率下拉框 `currentIndexChanged` 触发 `_on_port_config_changed()`
- `_on_port_config_changed()` 构造当前 `PortConfig`，更新 `_config.port_config`，调用 `save_device()`
- 应用启动加载设备时，若 `cfg.port_config` 不为 None，自动设置对应下拉框的当前项

---

### F2：高级串口参数弹窗

#### 触发方式
标题行现有按钮组（刷新、删除）旁增加一个 `ToolButton`，图标使用 Fluent 图标库 `FluentIcon.SETTING`（或 gear 图标），工具提示"高级串口参数"。

#### 弹窗（`_AdvancedPortDialog(QDialog)`）
位于 `device_list_panel.py` 文件内，作为内部类。

弹窗布局：

```
数据位:  [下拉: 5 / 6 / 7 / 8]
校验位:  [下拉: None / Even / Odd / Mark / Space]
停止位:  [下拉: 1 / 1.5 / 2]
         [取消]  [确定]
```

- 打开时用当前 `cfg.port_config` 初始化（为 None 则使用默认值 8/N/1）
- 点击"确定"：更新 `cfg.port_config` 的 `bytesize`/`parity`/`stopbits`，调用 `save_device()`
- 点击"取消"：不做任何修改
- 若当前已连接，不允许修改（按钮禁用，提示先断开连接）

---

### F3：设备名双击内联编辑

#### 实现方式
在 `device_list_panel.py` 中新增内部类 `_EditableLabel(QStackedWidget)`：

- 层 0：`StrongBodyLabel`（正常显示）
- 层 1：`LineEdit`（编辑状态，宽度填满）

行为：

- 双击 Label → 切到层 1，LineEdit 获焦，选中全部文字
- 回车 / 失焦（`editingFinished`）→ 验证非空，更新 `cfg.name`，切回层 0，调用 `save_device()`
- Esc → 恢复原值，切回层 0，不保存
- 若当前已连接，双击不触发编辑（连接时设备名不允许修改，防止与右侧 tab 标题不一致）

`DeviceCard` 标题行将现有 `StrongBodyLabel(self._config.name)` 替换为 `_EditableLabel(self._config.name)`。

---

### F4：卡片拖拽排序

#### 触发控件
标题行最左侧（在状态圆点左侧）增加拖拽手柄 `ToolButton`，显示 `⠿` 字符，光标设为 `Qt.SizeVerCursor`，无边框无背景。

#### 拖拽实现
`DeviceCard` 层：

- 手柄的 `mousePressEvent` 启动 `QDrag`
- MIME 类型：`application/x-device-id`，数据为 `device_id` 的 UTF-8 编码
- 拖拽时创建半透明卡片截图作为 drag pixmap

`DeviceListPanel` 层：

- 容器 `_scroll_widget` 调用 `setAcceptDrops(True)`
- 实现 `dragEnterEvent`：accept 自定义 MIME
- 实现 `dragMoveEvent`：计算插入位置，提供视觉指示线（用 `QRubberBand` 或简单横线绘制）
- 实现 `dropEvent`：
  1. 解析 `device_id`
  2. 找到对应卡片在 `_cards` 中的当前位置
  3. 根据鼠标位置计算目标插入位置
  4. 重排 `_cards` 列表
  5. 重建 layout（先清空再按新顺序 `addWidget`）
  6. 批量保存：遍历 `_cards`，以新索引为 `sort_order` 调用 `save_device()`

---

### F5：Bug 修复 — ParsedRecord.port 写死为空字符串

**文件**: `app/storage/repository.py`、`app/serial/worker.py`

`Frame` 对象本身无 `port` 字段，worker 调用处 `self.config.port` 可用（见 `worker.py:129`）。

修复方案：

- `repository.save(device_id, frame)` 签名改为 `save(device_id, frame, port: str = "")`
- `worker.py:132` 调用改为 `self._repository.save(self.device_id, frame, self.config.port)`
- `ParsedRecord` 的 `port` 字段改为传入的 `port` 参数

---

## 文件变更清单

| 文件 | 变更类型 | 变更内容 |
|---|---|---|
| `app/models/db.py` | 修改 | `DeviceRecord` 新增5个串口字段 |
| `app/storage/repository.py` | 修改 | `save_device`/`load_devices` 处理 `port_config`；修复 `port` 写死 bug |
| `app/ui/device_list_panel.py` | 修改 | 新增 `_EditableLabel`、`_AdvancedPortDialog`、拖拽手柄、拖拽事件处理、端口配置自动保存回填 |
| `migrations/versions/xxxx_add_port_config_to_devices.py` | 新增 | Alembic 迁移，为 `devices` 表添加串口配置字段 |

`app/models/domain.py` 无需修改（`PortConfig` 已完整）。

---

## 验证方式

1. **串口配置持久化**：选择串口和波特率后重启应用，验证下拉框自动回填到上次选择的值
2. **高级参数弹窗**：点击 ⚙ 按钮，修改数据位，重启后验证配置被保留
3. **名称编辑**：双击设备名，输入新名称，回车，重启应用验证名称持久化
4. **名称编辑取消**：双击后按 Esc，验证名称未改变
5. **拖拽排序**：拖拽卡片换位，重启应用验证顺序被保留
6. **连接中保护**：连接设备后验证名称无法双击编辑，高级参数按钮禁用
7. **数据库迁移**：`alembic upgrade head` 成功，新字段存在于 `devices` 表
