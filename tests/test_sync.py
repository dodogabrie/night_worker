"""Unit tests for sync functions in ralph_loop.py."""

import logging
from pathlib import Path

from ralph_loop import sync_iter_logs, sync_output_zips, sync_output_status_files


# ---------------------------------------------------------------------------
# sync_iter_logs
# ---------------------------------------------------------------------------


class TestSyncIterLogs:
    def test_no_logs_dir_is_noop(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """When work_logs_dir doesn't exist, nothing happens."""
        nc_log = tmp_path / "nc" / "combined.log"
        offsets: dict[str, int] = {}

        sync_iter_logs(tmp_path / "nonexistent", nc_log, offsets, mock_job_logger)

        assert not nc_log.exists()
        assert offsets == {}

    def test_single_iter_log(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """A single iter-1.log produces a combined file with header + content."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "iter-1.log").write_text("line one\nline two\n")

        nc_log = tmp_path / "nc" / "combined.log"
        offsets: dict[str, int] = {}

        sync_iter_logs(logs_dir, nc_log, offsets, mock_job_logger)

        content = nc_log.read_text()
        assert "=== Iteration 1 started" in content
        assert "line one\nline two\n" in content
        assert offsets["iter-1.log"] == len("line one\nline two\n")

    def test_incremental_append(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """Only new bytes are appended on subsequent calls."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        iter_file = logs_dir / "iter-1.log"
        iter_file.write_text("chunk1\n")

        nc_log = tmp_path / "nc" / "combined.log"
        offsets: dict[str, int] = {}

        sync_iter_logs(logs_dir, nc_log, offsets, mock_job_logger)
        first_content = nc_log.read_text()

        # Append more data
        with iter_file.open("a") as f:
            f.write("chunk2\n")

        sync_iter_logs(logs_dir, nc_log, offsets, mock_job_logger)
        second_content = nc_log.read_text()

        # Header should appear once, chunk2 appended
        assert second_content.count("=== Iteration 1") == 1
        assert "chunk1\n" in second_content
        assert "chunk2\n" in second_content
        assert len(second_content) > len(first_content)

    def test_multiple_files_sorted(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """Multiple iter files are processed in sorted order, each with a header."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "iter-2.log").write_text("second\n")
        (logs_dir / "iter-1.log").write_text("first\n")
        (logs_dir / "iter-10.log").write_text("tenth\n")

        nc_log = tmp_path / "nc" / "combined.log"
        offsets: dict[str, int] = {}

        sync_iter_logs(logs_dir, nc_log, offsets, mock_job_logger)

        content = nc_log.read_text()
        # All three headers present
        assert "=== Iteration 1 " in content
        assert "=== Iteration 2 " in content
        assert "=== Iteration 10 " in content

        # Sorted order: iter-1, iter-10, iter-2 (lexicographic glob sort)
        pos_1 = content.index("Iteration 1 ")
        pos_10 = content.index("Iteration 10 ")
        pos_2 = content.index("Iteration 2 ")
        assert pos_1 < pos_10 < pos_2

    def test_empty_log_no_crash(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """An empty iter log file doesn't cause errors (size=0, offset=0 → skip)."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "iter-1.log").write_text("")

        nc_log = tmp_path / "nc" / "combined.log"
        offsets: dict[str, int] = {}

        sync_iter_logs(logs_dir, nc_log, offsets, mock_job_logger)

        # Empty file → size <= offset (0 <= 0), skipped entirely
        assert not nc_log.exists() or nc_log.read_text() == ""


# ---------------------------------------------------------------------------
# sync_output_zips
# ---------------------------------------------------------------------------


class TestSyncOutputZips:
    def test_skips_partial_zip(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """Files ending in .partial.zip are ignored."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "job1_v1.partial.zip").write_bytes(b"incomplete")

        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)
        synced: set[str] = set()

        sync_output_zips(output_dir, nc_out, synced, mock_job_logger)

        assert synced == set()
        assert not (nc_out / "job1_v1.partial.zip").exists()

    def test_skips_already_synced(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """Already-synced zip names are not copied again."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "job1_v1.zip").write_bytes(b"data")

        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)
        synced: set[str] = {"job1_v1.zip"}

        sync_output_zips(output_dir, nc_out, synced, mock_job_logger)

        # Not copied because already in synced set
        assert not (nc_out / "job1_v1.zip").exists()

    def test_copies_new_zip(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """New .zip files are copied and added to the synced set."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "job1_v1.zip").write_bytes(b"zipdata")

        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)
        synced: set[str] = set()

        sync_output_zips(output_dir, nc_out, synced, mock_job_logger)

        assert (nc_out / "job1_v1.zip").read_bytes() == b"zipdata"
        assert "job1_v1.zip" in synced

    def test_no_output_dir_is_noop(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        """When local_output_dir doesn't exist, nothing happens."""
        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)
        synced: set[str] = set()

        sync_output_zips(tmp_path / "nonexistent", nc_out, synced, mock_job_logger)

        assert synced == set()


# ---------------------------------------------------------------------------
# sync_output_status_files
# ---------------------------------------------------------------------------


class TestSyncOutputStatusFiles:
    def test_copies_status_files(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        (output_dir / "job1.status").write_text("done | 5 iterations")

        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)

        sync_output_status_files(output_dir, nc_out, mock_job_logger)

        assert (nc_out / "job1.status").read_text() == "done | 5 iterations"

    def test_no_output_dir_is_noop(
        self, tmp_path: Path, mock_job_logger: logging.Logger
    ) -> None:
        nc_out = tmp_path / "nc" / "output"
        nc_out.mkdir(parents=True)

        sync_output_status_files(tmp_path / "nonexistent", nc_out, mock_job_logger)

        assert list(nc_out.iterdir()) == []
