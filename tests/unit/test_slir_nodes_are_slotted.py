"""Gate: a SLIR class may not take an attribute that nobody declared (docs/design/ir.md 8.2).

SLIR is the input of code generation, and lowering writes each fact into the node that it
makes. `slots=True` makes that a rule: a late write onto a SLIR object raises
AttributeError at the site that made it. This is the twin of the SHIR gate
(`tests/unit/test_shir_nodes_are_slotted.py`), for every module of the package.

Every statement and every terminator carries a REQUIRED span (G9): the gate refuses a
default value on the `span` field.
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import types
from typing import Iterator, get_args

import pytest

import sushi_lang.semantics.slir as slir_pkg
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import LocalId

from test_shir_nodes_are_slotted import _shared_defaults


def _package_modules() -> Iterator[types.ModuleType]:
    yield slir_pkg
    for info in pkgutil.walk_packages(slir_pkg.__path__, slir_pkg.__name__ + "."):
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

_SPANNED = sorted([*get_args(st.SlirStatement), *get_args(tm.SlirTerminator)],
                  key=lambda cls: cls.__qualname__)


def test_the_package_has_its_classes() -> None:
    # Guards the tests below against a pass on an empty list.
    names = {cls.__name__ for cls in _CLASSES}
    assert {"LocalId", "BlockId", "BorrowId", "SlirFunction", "SlirLocal", "SlirBlock",
            "SlirProgram", "Local", "Downcast", "Copy", "Move", "Int", "Assign", "Call",
            "DropIfSet", "Direct", "Builtin", "Indirect", "Discriminant", "SwitchInt",
            "Return", "Trap", "Unreachable"} <= names


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_every_slir_class_is_a_slotted_dataclass(cls: type) -> None:
    assert dataclasses.is_dataclass(cls), f"{cls.__qualname__} is not a dataclass"
    assert "__slots__" in cls.__dict__, (
        f"{cls.__qualname__} has no __slots__; any attribute can be written onto it"
    )


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_no_slir_class_carries_a_dict(cls: type) -> None:
    assert not any("__dict__" in base.__dict__ for base in cls.__mro__), (
        f"{cls.__qualname__} has a __dict__; slots on the subclass alone do not close it"
    )


@pytest.mark.parametrize("cls", _CLASSES, ids=lambda c: c.__qualname__)
def test_no_slir_class_shares_a_mutable_default(cls: type) -> None:
    assert _shared_defaults(cls) == [], (
        f"{cls.__qualname__} holds a mutable default that all its instances share; "
        "use field(default_factory=...)"
    )


@pytest.mark.parametrize("cls", _SPANNED, ids=lambda c: c.__qualname__)
def test_every_statement_and_terminator_requires_a_span(cls: type) -> None:
    spans = [item for item in dataclasses.fields(cls) if item.name == "span"]
    assert spans, f"{cls.__qualname__} has no span field (G9)"
    assert spans[0].default is dataclasses.MISSING, f"{cls.__qualname__}.span has a default"
    assert spans[0].default_factory is dataclasses.MISSING, (
        f"{cls.__qualname__}.span has a default factory")


def test_a_stray_attribute_is_refused() -> None:
    local = LocalId(0)
    with pytest.raises((AttributeError, TypeError)):
        local.name = "x"  # type: ignore[attr-defined]
