"""The SHIR id allocator gives the same ids in the same order on every run (`ir.md` 11.3).

A golden dump prints ids. So an id comes only from a counter that its allocator owns, in
the order of the calls, and never from `id()` or from the order of a hash. These tests
read Python objects only; they do not run the compiler.
"""
from __future__ import annotations

from typing import Callable, TypeVar

import pytest

from sushi_lang.semantics.shir.ids import DeclId, IdAllocator, LocalId, ShirId

_KINDS: tuple[Callable[[int], object], ...] = (ShirId, LocalId, DeclId)

IdT = TypeVar("IdT")


def _take(allocator: IdAllocator[IdT], count: int) -> list[IdT]:
    return [allocator.allocate() for _ in range(count)]


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.__name__)
def test_the_ids_start_at_zero_and_are_dense(kind: Callable[[int], object]) -> None:
    assert _take(IdAllocator(kind), 5) == [kind(index) for index in range(5)]


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.__name__)
def test_two_fresh_allocators_give_the_same_sequence(kind: Callable[[int], object]) -> None:
    assert _take(IdAllocator(kind), 7) == _take(IdAllocator(kind), 7)


def test_each_allocator_owns_its_counter() -> None:
    locals_ = IdAllocator(LocalId)
    nodes = IdAllocator(ShirId)
    _take(locals_, 3)
    assert nodes.allocate() == ShirId(0)
    assert locals_.allocate() == LocalId(3)


def test_two_kinds_with_one_index_are_not_equal() -> None:
    assert ShirId(0) != LocalId(0)
    assert LocalId(0) != DeclId(0)
    assert DeclId(0) != ShirId(0)


def test_ids_of_one_kind_sort_by_their_index() -> None:
    assert sorted([LocalId(2), LocalId(0), LocalId(1)]) == [LocalId(0), LocalId(1), LocalId(2)]
