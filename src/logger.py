"""Central console and rotating-file logging configuration."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    *,
    log_directory: str | Path = "outputs/logs",
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure application logging once and return its named logger."""
    logger = logging.getLogger("library_catalog")
    logger.setLevel(level)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        directory / "application.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
