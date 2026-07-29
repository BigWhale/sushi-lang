# semantics/passes/infinite_types.py
"""Reject types that contain themselves by value (CE2095).

A type has a finite size only if every cycle in its containment graph passes
through an indirection. `Own@(T)`, a dynamic array `T[]` and `List@(T)` all store
their payload behind a pointer, so they break a cycle; a plain struct field, a
FIXED array element `T[N]` and an enum payload are all stored inline, so they do
not.

    struct Node:                 struct S:
        i32 value                    i32 value
        Own@(Node) next              S inner        # sizeof(S) = 4 + sizeof(S)
    # finite: pointer            # infinite: CE2095

This mirrors what Rust reports as E0072 ("recursive type has infinite size",
help: insert some indirection) and Go as "invalid recursive type: T refers to
itself". Both trace the chain as written rather than naming whichever type they
happened to notice first, so this does too.

Runs after Pass 1.7 (field types are concrete by then) and before Pass 1.8,
whose hash-registration topological sort would otherwise be the first thing to
notice the cycle -- and it noticed it as a bare `ValueError`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.collect import EnumTable, StructTable
    from sushi_lang.semantics.typesys import Type


# A node in the containment graph: ("struct", name) or ("enum", name).
Node = Tuple[str, str]


def _inline_targets(ty: 'Type') -> List[Node]:
    """Named types that `ty` stores INLINE, i.e. that contribute to its size.

    Returns an empty list for anything reached through a pointer -- that is the
    indirection which makes a recursive type finite. `Own@(T)` and `List@(T)` need
    no special case: they are ordinary structs whose payload field is a
    PointerType, so the recursion stops at the pointer on its own.
    """
    from sushi_lang.semantics.typesys import ArrayType, EnumType, StructType

    if isinstance(ty, StructType):
        return [("struct", ty.name)]
    if isinstance(ty, EnumType):
        # Enum payloads are stored inline in the variant, so an enum on a
        # by-value path keeps the path by-value.
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


def _is_ours(cycle: List[Node]) -> bool:
    """Whether this cycle is CE2095's to report.

    A cycle made only of enums is already CE2052's, which says the same thing in
    more specific terms ("recursive enum 'X' requires Own<T> indirection") and is
    emitted from the enum topological sort in Pass 1.8. Reporting it here too would
    pre-empt the better message with a worse one. Every cycle touching a struct is
    ours: that is the case that had no diagnostic at all.
    """
    return any(kind == "struct" for kind, _name in cycle)


def _format_chain(cycle: List[Node]) -> str:
    """Render a cycle the way Go does: 'A refers to B refers to A'."""
    names = [name for _kind, name in cycle]
    names.append(cycle[0][1])
    return " refers to ".join(names)


def check_infinite_size_types(struct_table: 'StructTable', enum_table: 'EnumTable',
                              reporter: 'Reporter') -> bool:
    """Report CE2095 for every by-value containment cycle.

    Args:
        struct_table: All struct types, with field types resolved (post Pass 1.7)
        enum_table: All enum types, with variant types resolved (post Pass 1.7)
        reporter: Diagnostic sink

    Returns:
        True if any cycle was reported.
    """
    # Iterative DFS with an explicit path so the diagnostic can name the chain.
    # Recursion is not an option here: the graph is exactly the one that used to
    # blow the Python stack.
    state: Dict[Node, int] = {}  # 0 = on the current path, 1 = fully explored
    reported: Set[frozenset] = set()
    found = False

    roots: List[Node] = [("struct", n) for n in struct_table.order]
    roots += [("enum", n) for n in enum_table.order]

    for root in roots:
        if root in state:
            continue

        path: List[Node] = []
        # (node, index of the next successor to visit)
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
                # Back edge: everything from `successor` to the top of the path
                # is one cycle.
                cycle = path[path.index(successor):]
                key = frozenset(cycle)
                if key not in reported and _is_ours(cycle):
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
