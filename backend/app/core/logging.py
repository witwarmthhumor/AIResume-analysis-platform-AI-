"""日志系统：统一配置 + 按模块分级 logger 工厂。

全项目唯一的日志出口（P0 清单项）。日志不打印简历正文、面试内容和 API Key（AGENTS 铁律）。
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """初始化根 logger：控制台必开；可选追加滚动文件（默认 5MB×3 份）。

    force=True 覆盖 uvicorn 启动时注入的日志配置，保证应用层 logger 生效。
    """
    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    handlers.append(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)

    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)


def get_logger(name: str) -> logging.Logger:
    """按模块取 logger（如 get_logger(__name__)），级别继承根配置。"""
    return logging.getLogger(name)
