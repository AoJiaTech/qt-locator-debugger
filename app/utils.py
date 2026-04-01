"""通用工具函数。"""


def modbus_crc16(data: bytes) -> bytes:
    """
    计算 Modbus RTU CRC16 校验值。

    :param data: 待校验的字节序列（不含 CRC）
    :return: 2 字节 CRC，小端序（低字节在前）
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, byteorder="little")


def build_modbus_frame(payload: bytes) -> bytes:
    """
    在 payload 末尾附加 Modbus CRC16，返回完整帧。

    :param payload: 不含 CRC 的原始字节
    :return: payload + CRC（2 字节）
    """
    return payload + modbus_crc16(payload)


def verify_modbus_crc(frame: bytes) -> bool:
    """
    校验完整 Modbus 帧（含末尾 2 字节 CRC）是否正确。

    :param frame: 含 CRC 的完整帧
    :return: True 表示校验通过
    """
    if len(frame) < 3:
        return False
    payload, received_crc = frame[:-2], frame[-2:]
    return modbus_crc16(payload) == received_crc
