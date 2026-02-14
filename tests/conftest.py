import logging
import logging.handlers
from pathlib import Path

import pytest


@pytest.fixture
def job_dirs(tmp_path: Path) -> dict[str, Path]:
    """Work dir structure for a single job."""
    dirs = {
        "root": tmp_path / "work" / "test-job",
        "input": tmp_path / "work" / "test-job" / "input",
        "output": tmp_path / "work" / "test-job" / "output",
        "tmp": tmp_path / "work" / "test-job" / "tmp",
        "logs": tmp_path / "work" / "test-job" / "tmp" / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def nc_dirs(tmp_path: Path) -> dict[str, Path]:
    """Fake Nextcloud output and log dirs."""
    dirs = {
        "output": tmp_path / "nc" / "output",
        "logs": tmp_path / "nc" / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def state_dirs(tmp_path: Path) -> dict[str, Path]:
    """State directory with running/done/failed/queue subdirs."""
    base = tmp_path / ".state"
    dirs = {
        "root": base,
        "running": base / "running",
        "done": base / "done",
        "failed": base / "failed",
        "queue": base / "queue",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def mock_job_logger() -> logging.Logger:
    """Logger that writes to a MemoryHandler (no files)."""
    logger = logging.getLogger(f"test-job-{id(object())}")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.MemoryHandler(capacity=1000, flushLevel=logging.CRITICAL)
    logger.addHandler(handler)
    yield logger
    for h in logger.handlers[:]:
        h.close()
        logger.removeHandler(h)
