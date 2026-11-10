"""Gate: a node of the AST, of SHIR or of SLIR may not take an attribute that nobody declared.

The AST is a record of syntax (`ir.md` 5). SHIR carries no computed fact (`ir.md` 7.3,
ruling S5): a pass writes its answer into its own side table. SLIR is the input of code
generation, and lowering writes each fact into the node that it makes (`ir.md` 8.2). So a
pass that needs to record a result declares a typed field for it, or keeps it in its own
table. It never writes a new attribute onto a node in passing.

`slots=True` makes that a rule: a late write raises AttributeError at the site that made
it. One rule holds for the three trees, and the three lists of classes are the only
difference: the dataclasses of `semantics/ast.py`, and every class of each module of the
SHIR package and of the SLIR package.

A default value that is a mutable object is ONE object for all instances, so a change
through one instance changes all of them. The gate refuses that for SHIR and SLIR.

Every statement and every terminator of SLIR carries a REQUIRED span (G9): the gate
refuses a default value on the `span` field.
"""
from __future__ import annotations

import dataclasses
import enum
import importlib
import inspect
import pkgutil
import types
from typing import Iterator, get_args

import pytest

import sushi_lang.semantics.shir as shir_pkg
import sushi_lang.semantics.slir as slir_pkg
from sushi_lang.semantics import ast as ast_mod
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import LocalId as SlirLocalId


def _package_modules(package: types.ModuleType) -> Iterator[types.ModuleType]:
    yield package
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        yield importlib.import_module(info.name)


def _package_classes(package: types.ModuleType) -> list[type]:
    return sorted(
        {
            obj
            for module in _package_modules(package)
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__
        },
        key=lambda cls: f"{cls.__module__}.{cls.__qualname__}",
    )


_AST = [
    obj
    for _, obj in inspect.getmembers(ast_mod, inspect.isclass)
    if dataclasses.is_dataclass(obj) and obj.__module__ == ast_mod.__name__
]
_SHIR = _package_classes(shir_pkg)
_SLIR = _package_classes(slir_pkg)
_ALL = [*_AST, *_SHIR, *_SLIR]
_SPANNED = sorted([*get_args(st.SlirStatement), *get_args(tm.SlirTerminator)],
                  key=lambda cls: cls.__qualname__)

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


def _id(cls: type) -> str:
    return f"{cls.__module__.split('.')[2]}.{cls.__qualname__}"


def test_each_tree_has_its_classes() -> None:
    # Guards the tests below against a pass on an empty list.
    assert len(_AST) > 60
    shir_names = {cls.__name__ for cls in _SHIR}
    assert {"ShirId", "LocalId", "DeclId", "IdAllocator",
            "ShirBody", "ShirParam", "ShirLocal",
            "Instance", "TemplateCheck", "Initializer",
            "ShirNode", "ShirBlock", "Variant", "PatBinding", "Arm"} <= shir_names
    slir_names = {cls.__name__ for cls in _SLIR}
    assert {"LocalId", "BlockId", "BorrowId", "SlirFunction", "SlirLocal", "SlirBlock",
            "SlirProgram", "Local", "Downcast", "Copy", "Move", "Int", "Assign", "Call",
            "DropIfSet", "Direct", "Builtin", "Indirect", "Discriminant", "SwitchInt",
            "Return", "Trap", "Unreachable"} <= slir_names


@pytest.mark.parametrize("cls", _ALL, ids=_id)
def test_every_node_class_declares_slots(cls: type) -> None:
    assert "__slots__" in cls.__dict__, (
        f"{cls.__qualname__} has no __slots__; any attribute can be written onto it"
    )


@pytest.mark.parametrize("cls", _SLIR, ids=_id)
def test_every_slir_class_is_a_dataclass(cls: type) -> None:
    assert dataclasses.is_dataclass(cls), f"{cls.__qualname__} is not a dataclass"


@pytest.mark.parametrize("cls", _ALL, ids=_id)
def test_no_node_class_carries_a_dict(cls: type) -> None:
    # A __dict__ anywhere in the MRO opens the class to any attribute again.
    assert not any("__dict__" in base.__dict__ for base in cls.__mro__), (
        f"{cls.__qualname__} has a __dict__; slots on the subclass alone do not close it"
    )


@pytest.mark.parametrize("cls", [*_SHIR, *_SLIR], ids=_id)
def test_no_node_class_shares_a_mutable_default(cls: type) -> None:
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


def test_a_stray_attribute_is_refused_on_each_tree() -> None:
    with pytest.raises(AttributeError):
        ast_mod.Name(loc=None, id="x").some_analysis_result = 42  # type: ignore[attr-defined]
    local = ShirLocal(id=ShirLocalId(0), name="x", declared=None, loc=None)
    with pytest.raises(AttributeError):
        local.inferred_type = None  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        SlirLocalId(0).name = "x"  # type: ignore[attr-defined]
