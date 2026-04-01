from pathlib import Path

from loguru import logger


def setup_logger(log_dir: str | Path = "logs") -> None:
    """配置 loguru：控制台输出 + 按天滚动的文件输出。"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 移除默认 sink，统一格式
    logger.remove()

    logger.add(
        sink=lambda msg: print(msg, end=""),
        level="DEBUG",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )

    logger.add(
        sink=log_path / "serial_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",  # 每天零点滚动
        retention="30 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    )


# 供其他模块直接 from app.logger import logger 使用
__all__ = ["logger", "setup_logger"]
