"""激光位移传感器 Modbus RTU 响应解析器。"""

import struct

from app.utils import verify_modbus_crc
from app.serial.parser import BaseParser


class LaserDisplacementParser(BaseParser):
    """
    解析激光位移传感器的 Modbus RTU 响应帧。

    读取指令响应示例：
        01 03 04 B8 47 00 00 6E 86
        ↑addr ↑func ↑byte_count ↑data(4字节) ↑CRC(2字节)

    写单寄存器 echo（FC=0x06）示例：
        01 06 00 00 01 90 88 36
        ↑addr ↑func ↑reg_hi ↑reg_lo ↑val_hi ↑val_lo ↑CRC(2字节)

    数据字段（4 字节，共 2 个寄存器）：
        data[0:2] = 低位寄存器  (B8 47)
        data[2:4] = 高位寄存器  (00 00)
        合并为 32 位整数 = (高位 << 16) | 低位，除以 1000 得到 mm。

    内部维护粘包缓冲区，处理数据未对齐的情况。
    """

    # 功能码
    FC_READ = 0x03
    FC_WRITE_SINGLE = 0x06
    # 异常响应功能码（最高位置 1）
    FC_EXCEPTION_BASE = 0x80

    def __init__(self) -> None:
        self._buf = bytearray()

    def parse(self, data: bytes) -> dict:
        self._buf.extend(data)

        # 尝试从缓冲区中解析完整帧
        while len(self._buf) >= 5:  # 最小有效帧：addr + func + byte_count + 0字节数据 + 2CRC
            addr = self._buf[0]
            func = self._buf[1]

            # 异常响应：固定 5 字节
            if func & 0x80:
                if len(self._buf) < 5:
                    break
                frame = bytes(self._buf[:5])
                if verify_modbus_crc(frame):
                    del self._buf[:5]
                    return {
                        "type": "exception",
                        "addr": addr,
                        "func": func & 0x7F,
                        "error_code": frame[2] if len(frame) > 2 else 0,
                    }
                else:
                    # CRC 不对，丢弃一个字节重新搜索
                    del self._buf[0]
                    continue

            # 写单寄存器 echo：固定 8 字节
            if func == self.FC_WRITE_SINGLE:
                if len(self._buf) < 8:
                    break
                frame = bytes(self._buf[:8])
                if verify_modbus_crc(frame):
                    reg = (frame[2] << 8) | frame[3]
                    val = (frame[4] << 8) | frame[5]
                    del self._buf[:8]
                    return {
                        "type": "write_ack",
                        "addr": addr,
                        "register": reg,
                        "value": val,
                    }
                else:
                    del self._buf[0]
                    continue

            # 读保持寄存器响应
            if func == self.FC_READ:
                if len(self._buf) < 3:
                    break
                byte_count = self._buf[2]
                total_len = 3 + byte_count + 2  # addr+func+byte_count + data + CRC

                if len(self._buf) < total_len:
                    break  # 等待更多数据

                frame = bytes(self._buf[:total_len])
                if not verify_modbus_crc(frame):
                    # CRC 不对，丢弃一个字节重新对齐
                    del self._buf[0]
                    continue

                del self._buf[:total_len]
                payload = frame[3 : 3 + byte_count]
                return self._parse_read_response(addr, byte_count, payload)

            # 未知功能码，丢弃一个字节
            del self._buf[0]

        return {"status": "buffering"}

    @staticmethod
    def _parse_read_response(addr: int, byte_count: int, payload: bytes) -> dict:
        """解析读保持寄存器的数据段。"""
        if byte_count == 8 and len(payload) == 8:
            # 响应示例：01 03 08 40 9F BA 0B 00 00 00 00 41 45
            # payload[0:4] 为 IEEE 754 大端浮点测量值，payload[4:8] 保留
            raw_distance = struct.unpack(">f", payload[0:4])[0]
            distance_mm = 4.375 * (raw_distance - 4)
            return {
                "type": "distance",
                "addr": addr,
                "distance_mm": distance_mm,
                "raw_hex": payload[0:4].hex(),
            }

        # 其他长度：按寄存器逐个展示
        regs = []
        for i in range(0, len(payload) - 1, 2):
            regs.append(struct.unpack_from(">H", payload, i)[0])
        return {
            "type": "registers",
            "addr": addr,
            "registers": regs,
        }
