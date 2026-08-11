"""Shared storage locations and validation for dashboard timeframes."""
from pathlib import Path

from .config import DATA_DIR

TIMEFRAMES = ("1d", "4h")


def validate_timeframe(timeframe: str) -> str:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return timeframe


def output_dir(timeframe: str) -> Path:
    """Return the output root. Daily paths stay unchanged for compatibility."""
    validate_timeframe(timeframe)
    return DATA_DIR if timeframe == "1d" else DATA_DIR / timeframe


def json_dir(timeframe: str) -> Path:
    return output_dir(timeframe) / "json"


def csv_dir(timeframe: str) -> Path:
    return output_dir(timeframe) / "csv"


def screening_file(timeframe: str) -> Path:
    return output_dir(timeframe) / "screening" / "latest.json"
