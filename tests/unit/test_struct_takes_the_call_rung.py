"""Which callee takes a bare call `S(...)` when a struct `S` exists (docs/design/ir.md 7.4).

The rungs are those of the call ladder of the typecheck pass, in its order: a concrete
function of the unit, then a generic function of the scope (own or imported), then the
struct (#1076, ruling R30). Lowering asks `DeclLookup.constructs_struct` over the tables.
The tests call it over tables built by hand; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from sushi_lang.semantics.shir.lookup import DeclLookup

UNIT = "main"


class _Funcs:
    """A function table with one view: the name and the unit that declares it."""

    def __init__(self, declared: dict[str, str]) -> None:
        self._declared = declared

    def declared(self, name: str, unit: str) -> Optional[object]:
        return object() if self._declared.get(name) == unit else None

    def lookup(self, name: str, _unit: Optional[str] = None,
               _scope: object = None) -> Optional[object]:
        return object() if name in self._declared else None


def _lookup(*, concrete: Optional[str] = None, generic: Optional[str] = None,
            generic_struct: bool = False) -> DeclLookup:
    """The tables of a program with the struct `Crate`, and a function `Crate` of the unit
    `concrete` or a generic function `Crate` of the unit `generic`."""
    structs: dict = {} if generic_struct else {"Crate": SimpleNamespace(fields=())}
    generic_structs: dict = {"Crate": SimpleNamespace(fields=())} if generic_struct else {}
    funcs = _Funcs({} if concrete is None else {"Crate": concrete})
    generic_funcs = _Funcs({} if generic is None else {"Crate": generic})
    return DeclLookup({}, {}, structs, generic_structs, {},  # type: ignore[arg-type]
                      generic_funcs, None, None, funcs, UNIT)  # type: ignore[arg-type]


@pytest.mark.parametrize("generic_struct", [False, True])
def test_a_struct_takes_the_call_from_a_concrete_function_of_another_unit(
        generic_struct: bool) -> None:
    assert _lookup(concrete="other", generic_struct=generic_struct).constructs_struct(
        object(), "Crate")


def test_a_concrete_function_of_the_unit_takes_the_call() -> None:
    assert not _lookup(concrete=UNIT).constructs_struct(object(), "Crate")


@pytest.mark.parametrize("unit", [UNIT, "other"])
def test_a_generic_function_of_the_scope_takes_the_call(unit: str) -> None:
    assert not _lookup(generic=unit).constructs_struct(object(), "Crate")
