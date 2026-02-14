"""Integration tests for start_job using a FakePopen."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ralph_loop import start_job


class FakePopen:
    """
    Simulates subprocess.Popen for a docker container.

    On construction: writes iter-1.log into the work dir's tmp/logs/.
    poll() returns None twice, then 0 (or the configured return code).
    """

    def __init__(self, rc: int, work_logs_dir: Path, output_dir: Path):
        self._rc = rc
        self._poll_count = 0
        self.pid = 12345
        self.returncode: int | None = None
        self._work_logs_dir = work_logs_dir
        self._output_dir = output_dir

        # Simulate container writing log files
        self._work_logs_dir.mkdir(parents=True, exist_ok=True)
        (self._work_logs_dir / "iter-1.log").write_text("iteration 1 output\n")

    def poll(self) -> int | None:
        self._poll_count += 1
        if self._poll_count <= 2:
            return None
        self.returncode = self._rc
        return self._rc


def _make_fake_popen(rc: int, job_id: str, work_dir: Path, create_zip: bool = False):
    """Return a factory that creates a FakePopen and intercepts Popen calls."""
    logs_dir = work_dir / job_id / "tmp" / "logs"
    output_dir = work_dir / job_id / "output"

    def factory(cmd, **kwargs):
        fake = FakePopen(rc, logs_dir, output_dir)
        if create_zip:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{job_id}_v1.zip").write_bytes(b"PK\x03\x04fake")
        return fake

    return factory


@pytest.fixture
def integration_dirs(tmp_path: Path) -> dict[str, Path]:
    """Set up all dirs needed by start_job."""
    dirs = {
        "script_dir": tmp_path / "script",
        "nc_output": tmp_path / "nc" / "output",
        "nc_logs": tmp_path / "nc" / "logs",
        "state": tmp_path / ".state",
        "work": tmp_path / "work",
        "log": tmp_path / "logs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    # State subdirs
    for sub in ("running", "done", "failed", "queue"):
        (dirs["state"] / sub).mkdir(parents=True, exist_ok=True)

    # Job log subdir
    (dirs["log"] / "jobs").mkdir(parents=True, exist_ok=True)

    # Task prompt
    prompt = dirs["script_dir"] / "task_prompt.txt"
    prompt.write_text("do the thing\n")
    dirs["task_prompt"] = prompt

    # Input zip
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    zip_path = input_dir / "testjob.zip"
    zip_path.write_bytes(b"PK\x03\x04fake-zip-data")
    dirs["zip"] = zip_path

    return dirs


class TestStartJobSuccess:
    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_success_flow(
        self,
        mock_popen: MagicMock,
        mock_sleep: MagicMock,
        integration_dirs: dict[str, Path],
    ) -> None:
        d = integration_dirs
        job_id = "testjob"

        mock_popen.side_effect = _make_fake_popen(
            rc=0, job_id=job_id, work_dir=d["work"], create_zip=True
        )

        start_job(
            script_dir=d["script_dir"],
            nc_output_dir=d["nc_output"],
            task_prompt_file=d["task_prompt"],
            state_dir=d["state"],
            job_id=job_id,
            zip_path=d["zip"],
            work_dir=d["work"],
            log_dir=d["log"],
            nc_log_dir=d["nc_logs"],
            log_sync_seconds=0,
            keep_work_dir="on_failure",
        )

        # Combined log synced to nc_log_dir
        nc_log = d["nc_logs"] / f"{job_id}.log"
        assert nc_log.exists()
        log_content = nc_log.read_text()
        assert "Iteration 1" in log_content
        assert "iteration 1 output" in log_content

        # Status file written
        nc_status = d["nc_logs"] / f"{job_id}.status"
        assert nc_status.exists()
        assert "done" in nc_status.read_text()

        # Output zip synced
        assert (d["nc_output"] / f"{job_id}_v1.zip").exists()

        # State markers: running removed, done created
        assert not (d["state"] / "running" / job_id).exists()
        assert (d["state"] / "done" / job_id).exists()
        assert not (d["state"] / "failed" / job_id).exists()

        # Work dir cleaned up on success (on_failure mode)
        assert not (d["work"] / job_id).exists()

        # time.sleep was called (not real sleep due to mock)
        assert mock_sleep.called


class TestStartJobFailure:
    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_failure_flow(
        self,
        mock_popen: MagicMock,
        mock_sleep: MagicMock,
        integration_dirs: dict[str, Path],
    ) -> None:
        d = integration_dirs
        job_id = "testjob"

        mock_popen.side_effect = _make_fake_popen(
            rc=1, job_id=job_id, work_dir=d["work"]
        )

        start_job(
            script_dir=d["script_dir"],
            nc_output_dir=d["nc_output"],
            task_prompt_file=d["task_prompt"],
            state_dir=d["state"],
            job_id=job_id,
            zip_path=d["zip"],
            work_dir=d["work"],
            log_dir=d["log"],
            nc_log_dir=d["nc_logs"],
            log_sync_seconds=0,
            keep_work_dir="on_failure",
        )

        # State markers: running removed, failed created
        assert not (d["state"] / "running" / job_id).exists()
        assert not (d["state"] / "done" / job_id).exists()
        assert (d["state"] / "failed" / job_id).exists()

        # Status file indicates failure
        nc_status = d["nc_logs"] / f"{job_id}.status"
        assert nc_status.exists()
        assert "failed" in nc_status.read_text()

        # Work dir kept (on_failure mode + failure)
        assert (d["work"] / job_id).exists()


class TestStartJobKeepAlways:
    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_keep_always(
        self,
        mock_popen: MagicMock,
        mock_sleep: MagicMock,
        integration_dirs: dict[str, Path],
    ) -> None:
        d = integration_dirs
        job_id = "testjob"

        mock_popen.side_effect = _make_fake_popen(
            rc=0, job_id=job_id, work_dir=d["work"]
        )

        start_job(
            script_dir=d["script_dir"],
            nc_output_dir=d["nc_output"],
            task_prompt_file=d["task_prompt"],
            state_dir=d["state"],
            job_id=job_id,
            zip_path=d["zip"],
            work_dir=d["work"],
            log_dir=d["log"],
            nc_log_dir=d["nc_logs"],
            log_sync_seconds=0,
            keep_work_dir="always",
        )

        # Work dir kept even on success
        assert (d["work"] / job_id).exists()
        assert (d["state"] / "done" / job_id).exists()


class TestStartJobNoNcLogDir:
    @patch("time.sleep")
    @patch("subprocess.Popen")
    def test_no_nc_log_dir(
        self,
        mock_popen: MagicMock,
        mock_sleep: MagicMock,
        integration_dirs: dict[str, Path],
    ) -> None:
        """start_job works fine when nc_log_dir is None."""
        d = integration_dirs
        job_id = "testjob"

        mock_popen.side_effect = _make_fake_popen(
            rc=0, job_id=job_id, work_dir=d["work"]
        )

        start_job(
            script_dir=d["script_dir"],
            nc_output_dir=d["nc_output"],
            task_prompt_file=d["task_prompt"],
            state_dir=d["state"],
            job_id=job_id,
            zip_path=d["zip"],
            work_dir=d["work"],
            log_dir=d["log"],
            nc_log_dir=None,
            log_sync_seconds=0,
            keep_work_dir="on_failure",
        )

        assert (d["state"] / "done" / job_id).exists()
