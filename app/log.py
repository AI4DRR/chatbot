"""Logging configuration.

Standard-library ``logging`` only. Modules obtain a logger with
``logging.getLogger(__name__)``; nothing prints to stdout directly. The level
comes from ``LOG_LEVEL`` (see ``app.config``). This replaces the legacy
``debug_logger`` helpers: use ``logger.debug`` for what they printed.
"""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once. Safe to call repeatedly."""
    logging.basicConfig(level=level, format=_FORMAT, force=True)
    # Uvicorn installs its own handlers; keep its access log at the same level.
    logging.getLogger("uvicorn.access").setLevel(level)
