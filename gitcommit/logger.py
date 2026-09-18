"""Application logging to file and stderr."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_path: Path, verbose: bool = False) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gitcommit")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("gitcommit")


def read_log_tail(log_path: Path, lines: int = 100) -> list[str]:
    if not log_path.is_file():
        return []
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        content = fh.readlines()
    return [line.rstrip("\n") for line in content[-lines:]]
