"""Gate: an AST node may not take an attribute nobody declared.

The AST is a record of syntax (IR.md section 5). A pass that needs to record an
analysis result declares a typed field for it, or keeps it in its own table -- it does
not write a new attribute onto a node in passing.

`slots=True` is what makes that a rule rather than a habit: a stray write raises
AttributeError at the site that made it.
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

from sushi_lang.semantics import ast as ast_mod

_NODE_CLASSES = [
    obj
    for _, obj in inspect.getmembers(ast_mod, inspect.isclass)
    if dataclasses.is_dataclass(obj) and obj.__module__ == ast_mod.__name__
]


def test_the_module_actually_has_node_classes() -> None:
    # Guards the two tests below against silently passing on an empty list.
    assert len(_NODE_CLASSES) > 60


@pytest.mark.parametrize("cls", _NODE_CLASSES, ids=lambda c: c.__name__)
def test_every_ast_dataclass_declares_slots(cls: type) -> None:
    assert "__slots__" in cls.__dict__, (
        f"{cls.__name__} is missing slots=True; it can be stamped with any attribute"
    )


@pytest.mark.parametrize("cls", _NODE_CLASSES, ids=lambda c: c.__name__)
def test_no_ast_dataclass_carries_a_dict(cls: type) -> None:
    # A __dict__ anywhere in the MRO re-opens the class to arbitrary attributes, which
    # is exactly what slots is here to prevent.
    assert not any("__dict__" in base.__dict__ for base in cls.__mro__), (
        f"{cls.__name__} still has a __dict__; slots on the subclass alone is not enough"
    )


def test_a_stray_attribute_is_refused() -> None:
    node = ast_mod.Name(loc=None, id="x")
    with pytest.raises(AttributeError):
        node.some_analysis_result = 42  # type: ignore[attr-defined]
