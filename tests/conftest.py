"""Shared fixtures.

The instance registry (#115) writes a small record per process. Without this
fixture every test that builds a shelf-scoped input would leave one in the
real state directory of whoever ran the suite.

The served-code caches (#125, #158) are per process too: a hash computed by
one test must not stand in for the fixture of the next, and an opt-out left in
the environment must not silence a test that expects the warning.
"""

from __future__ import annotations

import pytest

from memshelf_mcp import instances, served


@pytest.fixture(autouse=True)
def _isolated_instance_registry(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(instances.STATE_DIR_ENV, str(tmp_path_factory.mktemp("memshelf-state")))
    instances._reset_for_tests()
    yield
    instances._reset_for_tests()


@pytest.fixture(autouse=True)
def _fresh_served_code_caches(monkeypatch):
    monkeypatch.delenv(served.WARNING_ENV, raising=False)
    served._reset_for_tests()
    yield
    served._reset_for_tests()
