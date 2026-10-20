"""The one walk over a SHIR tree (docs/design/ir.md 7.3, 11.3).

`walk` gives each value of the SHIR package under a root, the root too: a node, a part
that a node holds (an argument, a call target, a loop source, an arm) and an id. A value
comes before the values that it holds, and the values of one field come in their order,
so the walk gives the same order on every run. It goes into a field, a list and a tuple:
no field holds a node in another container (gate:
`tests/unit/test_shir_node_unions_are_total.py`). A type that a node holds is syntax of
another package, and the walk does not go into it.

Each value comes with its path from the root: a field adds `.name`, an item of a list or
a tuple adds `[index]`. A block adds nothing: the path of a statement is the path of its
block and its index (`block[6].body[0]`).
"""
from __future__ import annotations

import dataclasses
from typing import Iterator

from sushi_lang.semantics.shir.nodes import ShirBlock

_PACKAGE = __name__.rpartition(".")[0] + "."


def walk(value: object, path: str) -> Iterator[tuple[str, object]]:
    """Each value of the SHIR package under `value`, with its path, before its parts."""
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk(item, f"{path}[{index}]")
        return
    if not (type(value).__module__.startswith(_PACKAGE) and dataclasses.is_dataclass(value)):
        return
    yield path, value
    if isinstance(value, ShirBlock):
        yield from walk(value.statements, path)
        return
    for each in dataclasses.fields(value):
        yield from walk(getattr(value, each.name), f"{path}.{each.name}")
