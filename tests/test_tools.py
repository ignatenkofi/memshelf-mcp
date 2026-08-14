import inspect

import pytest
from pydantic import ValidationError

from memshelf_mcp import tools
from memshelf_mcp.tools import SHELF_PATH_ENV, ShelfScopedInput, ShelveInput


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


# --- the default shelf ------------------------------------------------------
#
# A packaged host (Claude Desktop's .mcpb) can configure an extension only
# through the environment, so `$MEMSHELF_SHELF_PATH` names one global shelf and
# a call that names its own path overrides it.


def _shelf_scoped_models() -> list[type[ShelfScopedInput]]:
    """Every input model addressed at a shelf, discovered rather than listed.

    A model added later that declares its own ``shelf_path`` instead of
    inheriting is exactly the regression this roster is here to catch, so
    membership is decided by the field, not by the base class.
    """
    found = [
        obj
        for _, obj in inspect.getmembers(tools, inspect.isclass)
        if obj is not ShelfScopedInput
        and hasattr(obj, "model_fields")
        and "shelf_path" in obj.model_fields
    ]
    assert len(found) >= 12, sorted(m.__name__ for m in found)  # guard the guard
    return found


def test_every_shelf_input_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv(SHELF_PATH_ENV, "/global/shelf")
    for model in _shelf_scoped_models():
        assert model.model_validate(_minimal(model)).shelf_path == "/global/shelf", model.__name__


def test_an_explicit_path_beats_the_environment(monkeypatch):
    monkeypatch.setenv(SHELF_PATH_ENV, "/global/shelf")
    for model in _shelf_scoped_models():
        args = _minimal(model) | {"shelf_path": "/project/shelf"}
        assert model.model_validate(args).shelf_path == "/project/shelf", model.__name__


def test_a_blank_path_is_the_same_as_omitting_it(monkeypatch):
    monkeypatch.setenv(SHELF_PATH_ENV, "/global/shelf")
    args = _minimal(ShelveInput) | {"shelf_path": "   "}
    assert ShelveInput.model_validate(args).shelf_path == "/global/shelf"


def test_no_path_and_no_environment_is_a_fixable_error(monkeypatch):
    monkeypatch.delenv(SHELF_PATH_ENV, raising=False)
    for model in _shelf_scoped_models():
        with pytest.raises(ValidationError) as caught:
            model.model_validate(_minimal(model))
        message = str(caught.value)
        assert SHELF_PATH_ENV in message, model.__name__
        assert "shelf_path" in message, model.__name__


def _minimal(model: type[ShelfScopedInput]) -> dict:
    """The model's required fields with accepted stand-in values, minus the shelf."""
    values: dict = {}
    for name, field in model.model_fields.items():
        if name == "shelf_path" or not field.is_required():
            continue
        annotation = field.annotation
        if annotation is int:
            values[name] = 1
        elif annotation is bool:
            values[name] = False
        elif getattr(annotation, "__origin__", None) is list:
            values[name] = []
        else:
            values[name] = {"kind": "topic", "method": "discover"}.get(name, "placeholder")
    return values
