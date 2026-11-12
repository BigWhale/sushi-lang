"""The one SHIR walk costs time in proportion to the size of the tree, not its depth squared.

The test gives hand-built SHIR data to `walk`. It starts no compiler stage.
"""
from __future__ import annotations

import dataclasses
import inspect
import time

from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import walk as walk_module
from sushi_lang.semantics.shir.exprs import Binary, IntConst, Lit
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.stmts import Return
from sushi_lang.semantics.shir.walk import walk

_SPAN = Span(1, 1, 1, 1)
_OP = "+"


def _lit(n):
    return Lit(ShirId(n), _SPAN, IntConst(n))


def _binary(n, lhs, rhs):
    return Binary(ShirId(n), _SPAN, _OP, lhs, rhs)


def _left_nest(depth):
    node = _lit(0)
    for n in range(1, depth + 1):
        node = _binary(n, node, _lit(-n))
    return node


def _reference(value, path):
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _reference(item, f"{path}[{index}]")
        return
    if not (type(value).__module__.startswith("sushi_lang.semantics.shir") and dataclasses.is_dataclass(value)):
        return
    yield path, value
    if isinstance(value, ShirBlock):
        yield from _reference(value.statements, path)
        return
    for each in dataclasses.fields(value):
        yield from _reference(getattr(value, each.name), f"{path}.{each.name}")


def test_walk_matches_the_reference_on_a_small_tree():
    tree = ShirBlock(ShirId(99), _SPAN, [Return(ShirId(98), _SPAN, _left_nest(6)), Return(ShirId(97), _SPAN, _lit(5))])
    got = list(walk(tree, "block"))
    want = list(_reference(tree, "block"))
    assert len(got) > 20
    assert [(p, id(v)) for p, v in got] == [(p, id(v)) for p, v in want]


def test_walk_matches_the_reference_on_lists_and_tuples():
    tree = [(_lit(1), [_lit(2)]), _binary(3, _lit(4), _lit(5))]
    assert [(p, id(v)) for p, v in walk(tree, "x")] == [(p, id(v)) for p, v in _reference(tree, "x")]


def test_a_deep_nest_is_walked_in_linear_time():
    depth = 8000
    tree = _left_nest(depth)
    start = time.perf_counter()
    count = sum(1 for _ in walk(tree, "body"))
    elapsed = time.perf_counter() - start
    assert count > 3 * depth
    assert elapsed < 2.0, elapsed


def test_walk_has_no_recursive_yield():
    assert "yield from walk(" not in inspect.getsource(walk_module)
