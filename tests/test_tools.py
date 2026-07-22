import pytest
from pydantic import ValidationError

from memshelf_mcp.tools import ShelveInput


def test_defaults():
    p = ShelveInput(shelf_path="/x", slug="2026-07-22-s", kind="topic", digest="d")
    assert p.mode == "live"
    assert p.autocommit is True
    assert p.sections == {}
    assert p.tags == []


def test_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ShelveInput(shelf_path="/x", slug="s", kind="journal", digest="d")


def test_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        ShelveInput(shelf_path="/x", slug="s", kind="topic", digest="d", mode="archive")
