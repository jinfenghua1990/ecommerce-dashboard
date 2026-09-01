"""结构化日志。

约定：
- 全局唯一 stdout handler，时间戳按 ISO8601 UTC；
- 第三方日志（uvicorn/sqlalchemy）接管，level 与 root 一致；
- 业务代码 `from app.core.logging import get_logger` 后用 `log.info(...)` 等；
- 故意静默吞错的健康检查代码用 warning 级别记录，不再裸 except 后 pass。
"""
from __future__ import annotations

import logging
import sys


_CONFIGURED = False
_FORMAT = "%(asctime)sZ %(levelname)-7s %(name)s :: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """幂等：可重复调用，最终状态一致。"""
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(level.upper())
    # uvicorn 默认 propagates=True，复用 root handler
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """业务用 logger 工厂，自动触发配置。"""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
