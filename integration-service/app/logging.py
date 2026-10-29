"""Structured JSON logging via structlog."""

import logging
import sys

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    # third-party chatter stays out of the structured stream even at DEBUG
    for noisy in ("httpx", "httpcore", "uvicorn", "sqlalchemy.engine", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
