from abc import ABC, abstractmethod

from app.models.domain import Frame


class BaseParser(ABC):
    """串口数据解析器基类。子类实现 parse() 即可接入框架。"""

    @abstractmethod
    def parse(self, data: bytes) -> dict:
        """将原始字节解析为结构化字典，供 UI 显示和持久化使用。"""
        ...

    def to_record(self, frame: Frame) -> dict | None:
        """
        可选钩子：将 Frame 转换为持久化友好的标准字典。
        默认返回 None（不做额外转换）。子类可覆盖以自定义存储格式。
        """
        return None


class HexParser(BaseParser):
    """原始十六进制显示，不做任何语义解析。"""

    def parse(self, data: bytes) -> dict:
        return {"hex": data.hex(" ").upper()}


class AsciiParser(BaseParser):
    """将字节解码为 ASCII/UTF-8 文本，不可打印字符替换为 '.'。"""

    def parse(self, data: bytes) -> dict:
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
        return {"text": text}


# 注册表：UI 下拉菜单从此处读取可用解析器
BUILTIN_PARSERS: dict[str, type[BaseParser]] = {
    "Raw Hex": HexParser,
    "ASCII": AsciiParser,
}
