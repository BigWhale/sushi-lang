"""Gate: a SHIR class may not take an attribute that nobody declared.

SHIR carries no computed fact (`ir.md` 7.3, ruling S5): a pass writes its answer into its
own side table. `slots=True` makes that a rule: a late write onto a SHIR object raises
AttributeError at the site that made it. This is the twin of the AST gate
(`tests/unit/test_ast_nodes_are_slotted.py`), for every module of the package.

A default value that is a mutable object is ONE object for all instances, so a change
through one instance changes all of them. The gate refuses that too.
"""
from __future__ import annotations

import dataclasses
import enum
import importlib
import inspect
import pkgutil
import types
from typing import Iterator

import pytest

import sushi_lang.semantics.shir as shir_pkg
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.ids import LocalId


def _package_modules() -> Iterator[types.ModuleType]:
    yield shir_pkg
    for info in pkgutil.walk_packages(shir_pkg.__path__, shir_pkg.__name__ + "."):
        yield importlib.import_module(info.name)


_CLASSES = sorted(
    {
        obj
        for module in _package_modules()
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__
    },
    key=lambda cls: f"{cls.__module__}.{cls.__qualname__}",
)

_NOT_A_VALUE = (property, classmethod, staticmethod,
                types.MemberDescriptorType, types.GetSetDescriptorType)


def _is_immutable(value: object) -> bool:
    if isinstance(value, (type(None), bool, int, float, str, bytes, enum.Enum)):
        return True
    if isinstance(value, (tuple, frozenset)):
        return all(_is_immutable(item) for item in value)
    return (dataclasses.is_dataclass(value) and not isinstance(value, type)
            and type(value).__dataclass_params__.frozen)  # type: ignore[attr-defined]


def _shared_defaults(cls: type) -> list[str]:
    """The names of the defaults of `cls` that all its instances share and can change."""
    shared = []
    if dataclasses.is_dataclass(cls):
        for item in dataclasses.fields(cls):
            if item.default is not dataclasses.MISSING and not _is_immutable(item.default):
                shared.append(item.name)
    for name, value in vars(cls).items():
        if name.startswith("__") or inspect.isroutine(value) or isinstance(value, _NOT_A_VALUE):
            continue
        if not _is_immutable(value):
            shared.append(name)
    return shared


def test_the_package_has_its_classes() -> None:
    # Guards the tests below against a pass on an empty list.
    names = {cls.__name__ for cls in _CLASSES}
    assert {"ShirId", "LocalId", "DeclId", "IdAllocator",
            "ShirBody", "ShirParam", "ShirLocal",
            "Instance", "TemplateCheck", "Initializer",
            "ShirNode", "ShirBlock", "Variant", "PatBinding", "Arm"} <= names


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_every_shir_class_declares_slots(cls: type) -> None:
    assert "__slots__" in cls.__dict__, (
        f"{cls.__qualname__} has no __slots__; any attribute can be written onto it"
    )


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_no_shir_class_carries_a_dict(cls: type) -> None:
    # A __dict__ anywhere in the MRO opens the class to any attribute again.
    assert not any("__dict__" in base.__dict__ for base in cls.__mro__), (
        f"{cls.__qualname__} has a __dict__; slots on the subclass alone do not close it"
    )


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_no_shir_class_shares_a_mutable_default(cls: type) -> None:
    assert _shared_defaults(cls) == [], (
        f"{cls.__qualname__} holds a mutable default that all its instances share; "
        "use field(default_factory=...)"
    )


def test_a_stray_attribute_is_refused() -> None:
    local = ShirLocal(id=LocalId(0), name="x", declared=None, loc=None)
    with pytest.raises(AttributeError):
        local.inferred_type = None  # type: ignore[attr-defined]
