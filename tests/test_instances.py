"""Second-instance detection (#115).

The point of these tests is the *direction* of the report. The incident that
raised the issue had the newcomer's stderr on ``/dev/null``, so a detector
that speaks from the newcomer is mute exactly when it is needed. Here the
neighbour is a real second process, and the assertion is that the process
doing the *observing* is the one that speaks.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import pytest

from memshelf_mcp import instances


@pytest.fixture
def live_pid():
    """A real, running process that is not this one."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture
def dead_pid():
    """A pid that has certainly exited."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait(timeout=10)
    return proc.pid


def _plant(shelf: str, pid: int, started_at: str = "2026-09-07T08:51:47+00:00") -> None:
    """Write the record another instance would have written."""
    directory = instances._record_dir(shelf)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "started_at": started_at, "shelf": shelf}) + "\n",
        encoding="utf-8",
    )


def test_register_writes_outside_the_shelf(tmp_path):
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    record = instances.register(str(shelf))
    assert record is not None and record.is_file()
    # The shelf is a git repository; a stray file there would ride into a diff.
    assert shelf not in record.parents
    assert json.loads(record.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_observe_reports_a_live_neighbour(tmp_path, live_pid, caplog):
    shelf = str(tmp_path / "shelf")
    _plant(shelf, live_pid)
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        found = instances.observe(shelf)
    assert [i.pid for i in found] == [live_pid]
    assert f"pid {live_pid}" in caplog.text
    assert str(os.getpid()) in caplog.text  # says who is speaking


def test_observe_warns_once_per_set(tmp_path, live_pid, caplog):
    shelf = str(tmp_path / "shelf")
    _plant(shelf, live_pid)
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        instances.observe(shelf)
        instances.observe(shelf)
        instances.observe(shelf)
    # Every shelf-scoped call passes through observe(); a line per call would
    # bury the host log it is written into.
    assert caplog.text.count("another memshelf-mcp instance") == 1


def test_neighbour_on_another_shelf_is_silence(tmp_path, live_pid, caplog):
    """The negative that makes the positive mean something."""
    _plant(str(tmp_path / "other-shelf"), live_pid)
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        found = instances.observe(str(tmp_path / "this-shelf"))
    assert found == []
    assert "another memshelf-mcp instance" not in caplog.text


def test_dead_neighbour_is_silence_and_its_record_is_pruned(tmp_path, dead_pid, caplog):
    shelf = str(tmp_path / "shelf")
    _plant(shelf, dead_pid)
    record = instances._record_dir(shelf) / f"{dead_pid}.json"
    assert record.is_file()
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        assert instances.observe(shelf) == []
    assert "another memshelf-mcp instance" not in caplog.text
    assert not record.exists()


def test_same_shelf_reached_by_a_different_path_still_collides(tmp_path, live_pid, caplog):
    """A symlink is not a different shelf — grouping is by resolved path."""
    real = tmp_path / "real-shelf"
    real.mkdir()
    link = tmp_path / "link-shelf"
    link.symlink_to(real, target_is_directory=True)
    _plant(str(real), live_pid)
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        found = instances.observe(str(link))
    assert [i.pid for i in found] == [live_pid]


def test_registry_can_be_switched_off(tmp_path, live_pid, monkeypatch, caplog):
    monkeypatch.setenv(instances.DISABLE_ENV, "off")
    shelf = str(tmp_path / "shelf")
    _plant(shelf, live_pid)
    with caplog.at_level(logging.WARNING, logger=instances.logger.name):
        assert instances.observe(shelf) == []
    assert "another memshelf-mcp instance" not in caplog.text


def test_unwritable_state_dir_does_not_break_a_call(tmp_path, monkeypatch):
    """Detection is advisory: a read-only state directory must not refuse work."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setenv(instances.STATE_DIR_ENV, str(blocked))
    instances._reset_for_tests()
    assert instances.register(str(tmp_path / "shelf")) is None
    assert instances.observe(str(tmp_path / "shelf")) == []
