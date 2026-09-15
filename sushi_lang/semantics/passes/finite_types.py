"""Reject types that contain themselves by value (CE2095)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.collect import EnumTable, StructTable
    from sushi_lang.semantics.typesys import Type


Node = Tuple[str, str]
Marks = Tuple[int, int]


def _inline_targets(ty: 'Type') -> List[Node]:
    """Named types that `ty` stores INLINE, i.e. that contribute to its size."""
    from sushi_lang.semantics.typesys import ArrayType, EnumType, StructType

    if isinstance(ty, StructType):
        return [("struct", ty.name)]
    if isinstance(ty, EnumType):
        return [("enum", ty.name)]
    if isinstance(ty, ArrayType):
        # FIXED array: N elements stored inline. DynamicArrayType is deliberately
        # absent -- it owns a heap buffer, which is an indirection.
        return _inline_targets(ty.base_type)
    return []


def _successors(node: Node, struct_table: 'StructTable',
                enum_table: 'EnumTable') -> List[Node]:
    """Everything `node` contains by value, in declaration order."""
    kind, name = node
    out: List[Node] = []

    if kind == "struct":
        struct_type = struct_table.by_name.get(name)
        if struct_type is None:
            return out
        for _field_name, field_type in struct_type.fields:
            out.extend(_inline_targets(field_type))
    else:
        enum_type = enum_table.by_name.get(name)
        if enum_type is None:
            return out
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                out.extend(_inline_targets(assoc_type))

    return out


def _format_chain(cycle: List[Node]) -> str:
    """Render a cycle the way Go does: 'A refers to B refers to A'."""
    names = [name for _kind, name in cycle]
    names.append(cycle[0][1])
    return " refers to ".join(names)


def table_marks(struct_table: 'StructTable', enum_table: 'EnumTable') -> Marks:
    """How many names each table holds now.

    `check_infinite_size_types(since=marks)` then walks from the names appended after
    this point alone: the instances a late intern adds (#677).
    """
    return len(struct_table.order), len(enum_table.order)


def check_infinite_size_types(struct_table: 'StructTable', enum_table: 'EnumTable',
                              reporter: 'Reporter',
                              since: Optional[Marks] = None) -> bool:
    """Report CE2095 for every by-value containment cycle, once per cycle.

    Every kind the walk reaches is reported here: a struct field, a fixed-array element
    and an enum payload are all stored inline, so a pure enum cycle is the same fault as
    a struct one (#677). With `since`, the roots are the declarations appended after
    those marks. A cycle among older declarations stopped the analysis the first time,
    so a walk from the new names finds every new cycle and repeats none.
    """
    # Iterative DFS with an explicit path so the diagnostic can name the chain.
    # Recursion is not an option here: the graph is exactly the one that used to
    # blow the Python stack.
    state: Dict[Node, int] = {}  # 0 = on the current path, 1 = fully explored
    reported: Set[frozenset] = set()
    found = False

    struct_from, enum_from = since if since is not None else (0, 0)
    roots: List[Node] = [("struct", n) for n in struct_table.order[struct_from:]]
    roots += [("enum", n) for n in enum_table.order[enum_from:]]

    for root in roots:
        if root in state:
            continue

        path: List[Node] = []
        stack: List[Tuple[Node, int]] = [(root, 0)]
        state[root] = 0
        path.append(root)

        while stack:
            node, next_index = stack[-1]
            successors = _successors(node, struct_table, enum_table)

            if next_index >= len(successors):
                stack.pop()
                state[node] = 1
                path.pop()
                continue

            stack[-1] = (node, next_index + 1)
            successor = successors[next_index]

            if state.get(successor) == 0:
                cycle = path[path.index(successor):]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    found = True
                    _report(cycle, struct_table, enum_table, reporter)
            elif successor not in state:
                state[successor] = 0
                path.append(successor)
                stack.append((successor, 0))

    return found


def _report(cycle: List[Node], struct_table: 'StructTable', enum_table: 'EnumTable',
            reporter: 'Reporter') -> None:
    """Emit CE2095 against the declaration the cycle starts at."""
    kind, name = cycle[0]
    span: Optional[object] = (
        struct_table.spans.get(name) if kind == "struct"
        else enum_table.spans.get(name)
    )

    er.emit(reporter, er.ERR.CE2095, span, name=name, chain=_format_chain(cycle))
