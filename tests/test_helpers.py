"""Unit tests for helper functions in ralph_loop.py."""

import logging
from pathlib import Path

import pytest

from ralph_loop import _format_elapsed, atomic_copy, write_nc_status, _cleanup_work_dir


# ---------------------------------------------------------------------------
# _format_elapsed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0s"),
        (1, "1s"),
        (59, "59s"),
        (60, "1m00s"),
        (90, "1m30s"),
        (3599, "59m59s"),
        (3600, "1h00m"),
        (3661, "1h01m"),
        (7200, "2h00m"),
    ],
)
def test_format_elapsed(seconds: int, expected: str) -> None:
    assert _format_elapsed(seconds) == expected


# ---------------------------------------------------------------------------
# atomic_copy
# ---------------------------------------------------------------------------


def test_atomic_copy_basic(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dst = tmp_path / "dst.txt"

    atomic_copy(src, dst)

    assert dst.read_text() == "hello"
    # No leftover .tmp file
    assert not dst.with_suffix(".txt.tmp").exists()


def test_atomic_copy_creates_parent(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "deep" / "nested" / "dst.txt"

    atomic_copy(src, dst)

    assert dst.read_text() == "data"


def test_atomic_copy_overwrites(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("new")
    dst = tmp_path / "dst.txt"
    dst.write_text("old")

    atomic_copy(src, dst)

    assert dst.read_text() == "new"


# ---------------------------------------------------------------------------
# write_nc_status
# ---------------------------------------------------------------------------


def test_write_nc_status(tmp_path: Path) -> None:
    status_path = tmp_path / "logs" / "job1.status"

    write_nc_status(status_path, "running", "iter 3/8 | elapsed 2m00s")

    assert status_path.exists()
    content = status_path.read_text()
    assert content == "running | iter 3/8 | elapsed 2m00s\n"


def test_write_nc_status_creates_parent(tmp_path: Path) -> None:
    status_path = tmp_path / "deep" / "nested" / "job1.status"

    write_nc_status(status_path, "done", "8 iterations, 1h02m")

    assert status_path.read_text() == "done | 8 iterations, 1h02m\n"


# ---------------------------------------------------------------------------
# _cleanup_work_dir
# ---------------------------------------------------------------------------


def _make_work_dir(tmp_path: Path) -> Path:
    work = tmp_path / "work" / "job1"
    (work / "input").mkdir(parents=True)
    (work / "output").mkdir(parents=True)
    (work / "tmp").mkdir(parents=True)
    (work / "input" / "input.zip").write_bytes(b"fake")
    return work


class TestCleanupWorkDir:
    def test_always_success(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=True, keep_work_dir="always", jlog=mock_job_logger)
        assert work.exists()

    def test_always_failure(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=False, keep_work_dir="always", jlog=mock_job_logger)
        assert work.exists()

    def test_never_success(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=True, keep_work_dir="never", jlog=mock_job_logger)
        assert not work.exists()

    def test_never_failure(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=False, keep_work_dir="never", jlog=mock_job_logger)
        assert not work.exists()

    def test_on_failure_success(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=True, keep_work_dir="on_failure", jlog=mock_job_logger)
        assert not work.exists()

    def test_on_failure_failure(self, tmp_path: Path, mock_job_logger: logging.Logger) -> None:
        work = _make_work_dir(tmp_path)
        _cleanup_work_dir(work, success=False, keep_work_dir="on_failure", jlog=mock_job_logger)
        assert work.exists()
