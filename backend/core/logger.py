"""
logger.py — 结构化日志。

在标准库 logging 之上包装一层，支持 ``logger.info("event", key=value)`` 风格，
输出 ``event | key=value`` 的可读格式，便于按事件名检索排查。

用法：
    from backend.core.logger import get_logger
    log = get_logger(__name__)
    log.info("retry.succeeded", node="review", attempt=2)
"""
from __future__ import annotations

import logging
import sys

from backend.config import get_settings


class _Logger:
    """日志包装类：支持「事件名 + 键值对」的结构化写法，内部委托标准库 logging。"""

    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _fmt(self, event: str, **kw) -> str:
        if kw:
            return event + " | " + " ".join(f"{k}={v!r}" for k, v in kw.items())
        return event

    def debug(self, event: str, *args, **kw):
        if args:
            self._log.debug(event, *args)
        else:
            self._log.debug(self._fmt(event, **kw))

    def info(self, event: str, *args, **kw):
        if args:
            self._log.info(event, *args)
        else:
            self._log.info(self._fmt(event, **kw))

    def warning(self, event: str, *args, **kw):
        if args:
            self._log.warning(event, *args)
        else:
            self._log.warning(self._fmt(event, **kw))

    def error(self, event: str, **kw):
        exc_info = kw.pop("exc_info", False)
        self._log.error(self._fmt(event, **kw), exc_info=exc_info)

    def critical(self, event: str, **kw):
        self._log.critical(self._fmt(event, **kw))


def configure_logging() -> None:
    """全局日志配置：整个应用只在启动时（main.py）调用一次。

    设定格式、级别、控制台 + 文件双输出，并压低第三方库噪音日志。
    """
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file:
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        level=level,
        force=True,
    )

    # 压低第三方库的 INFO 噪音，避免刷屏
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


def get_logger(name: str) -> _Logger:
    """对外的工厂函数：每个模块用 get_logger(__name__) 拿到自己的日志器。"""
    return _Logger(name)
