"""Shared fixtures.

The instance registry (#115) writes a small record per process. Without this
fixture every test that builds a shelf-scoped input would leave one in the
real state directory of whoever ran the suite.
"""

from __future__ import annotations

import pytest

from memshelf_mcp import instances


@pytest.fixture(autouse=True)
def _isolated_instance_registry(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(instances.STATE_DIR_ENV, str(tmp_path_factory.mktemp("memshelf-state")))
    instances._reset_for_tests()
    yield
    instances._reset_for_tests()
