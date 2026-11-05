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

from sushi_lang.semantics.shir.nodes import ShirBlock, ShirNode

_PACKAGE = __name__.rpartition(".")[0] + "."


def _holds_nodes(value: object) -> bool:
    if isinstance(value, (list, tuple)):
        return True
    return type(value).__module__.startswith(_PACKAGE) and dataclasses.is_dataclass(value)


def walk(value: object, path: str) -> Iterator[tuple[str, object]]:
    """Each value of the SHIR package under `value`, with its path, before its parts."""
    stack = [(value, path)] if _holds_nodes(value) else []
    while stack:
        value, path = stack.pop()
        if isinstance(value, (list, tuple)):
            for index in range(len(value) - 1, -1, -1):
                item = value[index]
                if _holds_nodes(item):
                    stack.append((item, f"{path}[{index}]"))
            continue
        yield path, value
        if isinstance(value, ShirBlock):
            stack.append((value.statements, path))
            continue
        for each in reversed(dataclasses.fields(value)):  # type: ignore[arg-type]
            part = getattr(value, each.name)
            if _holds_nodes(part):
                stack.append((part, f"{path}.{each.name}"))


def parts(value: object) -> Iterator[ShirNode]:
    """Each node right under the node `value`: the walk of its fields, which stops at each
    node that it meets and does not go into it."""
    stack: list[object] = [getattr(value, each.name)
                           for each in reversed(dataclasses.fields(value))]  # type: ignore[arg-type]
    while stack:
        item = stack.pop()
        if isinstance(item, ShirNode):
            yield item
        elif isinstance(item, (list, tuple)):
            stack.extend(reversed(item))
        elif _holds_nodes(item):
            stack.extend(getattr(item, each.name)
                         for each in reversed(dataclasses.fields(item)))  # type: ignore[arg-type]
