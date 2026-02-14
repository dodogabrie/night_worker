"""Unit tests for trigger logic in ralph_loop.py."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph_loop import (
    resolve_trigger_path,
    resolve_persistent_trigger_path,
    should_fire_persistent_trigger,
    mark_persistent_trigger_handled,
    read_float,
    write_float,
)


# ---------------------------------------------------------------------------
# resolve_trigger_path
# ---------------------------------------------------------------------------


class TestResolveTriggerPath:
    def test_returns_none_when_env_unset(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_trigger_path(tmp_path) is None

    def test_returns_none_when_env_empty(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"START_TRIGGER_FILE": "  "}, clear=True):
            assert resolve_trigger_path(tmp_path) is None

    def test_relative_path_uses_script_dir(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"START_TRIGGER_FILE": "trigger.flag"}, clear=True):
            result = resolve_trigger_path(tmp_path)
            assert result == tmp_path / "trigger.flag"

    def test_relative_path_uses_trigger_dir(self, tmp_path: Path) -> None:
        trigger_dir = tmp_path / "triggers"
        trigger_dir.mkdir()
        with patch.dict(
            os.environ,
            {"START_TRIGGER_FILE": "go.flag", "START_TRIGGER_DIR": str(trigger_dir)},
            clear=True,
        ):
            result = resolve_trigger_path(tmp_path)
            assert result == trigger_dir / "go.flag"

    def test_absolute_path_used_directly(self, tmp_path: Path) -> None:
        abs_path = tmp_path / "abs" / "trigger"
        with patch.dict(
            os.environ, {"START_TRIGGER_FILE": str(abs_path)}, clear=True
        ):
            result = resolve_trigger_path(tmp_path)
            assert result == abs_path


# ---------------------------------------------------------------------------
# resolve_persistent_trigger_path
# ---------------------------------------------------------------------------


class TestResolvePersistentTriggerPath:
    def test_returns_none_when_env_unset(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_persistent_trigger_path(tmp_path) is None

    def test_relative_path_uses_script_dir(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ, {"PERSISTENT_TRIGGER_FILE": "cron.flag"}, clear=True
        ):
            result = resolve_persistent_trigger_path(tmp_path)
            assert result == tmp_path / "cron.flag"

    def test_relative_path_uses_persistent_trigger_dir(self, tmp_path: Path) -> None:
        trigger_dir = tmp_path / "ptriggers"
        trigger_dir.mkdir()
        with patch.dict(
            os.environ,
            {
                "PERSISTENT_TRIGGER_FILE": "nightly.flag",
                "PERSISTENT_TRIGGER_DIR": str(trigger_dir),
            },
            clear=True,
        ):
            result = resolve_persistent_trigger_path(tmp_path)
            assert result == trigger_dir / "nightly.flag"


# ---------------------------------------------------------------------------
# read_float / write_float
# ---------------------------------------------------------------------------


class TestReadWriteFloat:
    def test_read_missing_file(self, tmp_path: Path) -> None:
        assert read_float(tmp_path / "nope.txt") == 0.0

    def test_read_invalid_content(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.txt"
        f.write_text("not a number\n")
        assert read_float(f) == 0.0

    def test_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "val.txt"
        write_float(f, 1234567890.123)
        assert read_float(f) == pytest.approx(1234567890.123)


# ---------------------------------------------------------------------------
# should_fire_persistent_trigger
# ---------------------------------------------------------------------------


class TestShouldFirePersistentTrigger:
    def test_trigger_file_missing(self, tmp_path: Path) -> None:
        state = tmp_path / ".state"
        state.mkdir()
        assert should_fire_persistent_trigger(tmp_path / "nope", state) is False

    def test_first_touch_fires(self, tmp_path: Path) -> None:
        """First time trigger exists and no state → fires."""
        trigger = tmp_path / "trigger.flag"
        trigger.touch()
        state = tmp_path / ".state"
        (state / "trigger").mkdir(parents=True)

        assert should_fire_persistent_trigger(trigger, state) is True

    def test_same_mtime_does_not_fire(self, tmp_path: Path) -> None:
        """After marking handled, same mtime → does not fire."""
        trigger = tmp_path / "trigger.flag"
        trigger.touch()
        state = tmp_path / ".state"
        (state / "trigger").mkdir(parents=True)

        mark_persistent_trigger_handled(trigger, state)
        assert should_fire_persistent_trigger(trigger, state) is False

    def test_new_touch_fires_again(self, tmp_path: Path) -> None:
        """After marking handled, a new touch (newer mtime) → fires again."""
        trigger = tmp_path / "trigger.flag"
        trigger.touch()
        state = tmp_path / ".state"
        (state / "trigger").mkdir(parents=True)

        mark_persistent_trigger_handled(trigger, state)
        assert should_fire_persistent_trigger(trigger, state) is False

        # Ensure mtime advances (filesystem resolution can be 1s)
        time.sleep(0.05)
        trigger.write_text("touched again\n")

        assert should_fire_persistent_trigger(trigger, state) is True


# ---------------------------------------------------------------------------
# mark_persistent_trigger_handled
# ---------------------------------------------------------------------------


class TestMarkPersistentTriggerHandled:
    def test_creates_state_file(self, tmp_path: Path) -> None:
        trigger = tmp_path / "trigger.flag"
        trigger.touch()
        state = tmp_path / ".state"
        (state / "trigger").mkdir(parents=True)

        mark_persistent_trigger_handled(trigger, state)

        state_file = state / "trigger" / "trigger.flag.mtime"
        assert state_file.exists()
        stored = read_float(state_file)
        assert stored == pytest.approx(trigger.stat().st_mtime)

    def test_missing_trigger_is_noop(self, tmp_path: Path) -> None:
        state = tmp_path / ".state"
        (state / "trigger").mkdir(parents=True)

        # Should not raise
        mark_persistent_trigger_handled(tmp_path / "nope", state)
