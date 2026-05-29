"""Centralized logging configuration for the ad-research experiment platform.

Research-friendly defaults:
- INFO level by default (clean, useful output for normal day-to-day runs).
- Override with AD_RESEARCH_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR.
- Dedicated results path for primary researcher output (analysis tables,
  hypothesis conclusions, QA reports, etc.) that always lands on stdout.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL = os.getenv("AD_RESEARCH_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()

RESULTS_LOGGER_NAME = "ad_research.results"


def configure_logging() -> None:
    """Configure the root logger and results logger.

    Call this once at the very start of main() in experiment.py, analysis.py,
    qa.py, literature.py, etc.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="[%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)

    results = logging.getLogger(RESULTS_LOGGER_NAME)
    results.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a normal module logger for internal/infrastructure messages."""
    return logging.getLogger(name)


def get_results_logger() -> logging.Logger:
    """Return the dedicated results logger for primary researcher output."""
    return logging.getLogger(RESULTS_LOGGER_NAME)


def log_result(msg: str) -> None:
    """Emit a primary researcher-visible result.

    Uses direct print() so output format and visibility for tables, p-values,
    QA reports, "saved to ..." messages, etc. remain exactly as expected —
    always on stdout, unaffected by AD_RESEARCH_LOG_LEVEL.
    """
    print(msg)
