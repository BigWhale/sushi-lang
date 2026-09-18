"""Reject types that contain themselves by value (CE2095)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type_name

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.collect import EnumTable, StructTable
    from sushi_lang.semantics.typesys import Type


Node = Tuple[str, str]
Marks = Tuple[int, int]


def _inline_targets(ty: 'Type') -> List[Node]:
    """Named types that `ty` stores INLINE, i.e. that contribute to its size.

    The ONE type walk, with the two parameters this rule needs (#679): `inline_only`
    stops at every indirection, so a `DynamicArrayType` contributes nothing while a
    FIXED array contributes its element; `through_declarations=False` stops AT a named
    type, because the search below enters it as the next node and reads its own
    successors then.
    """
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.typesys import EnumType, StructType

    out: List[Node] = []
    for reached in walk_named_types(ty, inline_only=True, through_declarations=False):
        if isinstance(reached, StructType):
            out.append(("struct", reached.name))
        elif isinstance(reached, EnumType):
            out.append(("enum", reached.name))
    return out


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
    """Render a cycle the way Go does: 'A refers to B refers to A'.

    Every hop reads in the surface `@(...)` spelling: an instance is keyed by its
    internal `List<i32>` name, and no user-facing text carries that form.
    """
    names = [display_type_name(name) for _kind, name in cycle]
    names.append(display_type_name(cycle[0][1]))
    return " refers to ".join(names)


def table_marks(struct_table: 'StructTable', enum_table: 'EnumTable') -> Marks:
    """How many names each table holds now.

    `check_infinite_size_types(since=marks)` then walks from the names appended after
    this point alone: the instances a late intern adds (#677).
    """
    return len(struct_table.order), len(enum_table.order)


def names_since(struct_table: 'StructTable', enum_table: 'EnumTable',
                since: Optional[Marks] = None) -> Tuple[List[str], List[str]]:
    """The struct names and the enum names each table gained after `since`.

    One reader of the marks. This pass walks from these names, and the late interner
    narrows its resolve and derive runs to the same ones (#676), so the two cannot
    disagree about which instances a round added.
    """
    struct_from, enum_from = since if since is not None else (0, 0)
    return struct_table.order[struct_from:], enum_table.order[enum_from:]


def check_infinite_size_types(struct_table: 'StructTable', enum_table: 'EnumTable',
                              reporter: 'Reporter',
                              since: Optional[Marks] = None) -> bool:
    """Report CE2095 for every by-value containment cycle, once per cycle.

    Every kind the walk reaches is reported here: a struct field, a fixed-array element
    and an enum payload are all stored inline, so a pure enum cycle is the same fault as
    a struct one (#677). With `since`, the roots are the declarations appended after
    those marks. A cycle among older declarations stopped the analysis the first time,
    so a walk from the new names finds every new cycle and repeats none.

    The roots are every STRUCT and then every enum, which is what decides where a MIXED
    cycle is reported: a struct whose field is an enum whose payload is the struct reads
    at the struct, whichever of the two is declared first (#700). Declaration order is
    the other answer and is not the one taken -- a cycle has no first member, so the
    walk's order is as good a rule and it is the one the fixtures pin.
    """
    # Iterative DFS with an explicit path so the diagnostic can name the chain.
    # Recursion is not an option here: the graph is exactly the one that used to
    # blow the Python stack.
    state: Dict[Node, int] = {}  # 0 = on the current path, 1 = fully explored
    reported: Set[frozenset] = set()
    found = False

    new_structs, new_enums = names_since(struct_table, enum_table, since)
    roots: List[Node] = [("struct", n) for n in new_structs]
    roots += [("enum", n) for n in new_enums]

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
    """Emit CE2095 against the declaration the cycle starts at.

    A generic instance carries the span of the TEMPLATE it was minted from, stamped at
    the intern, because the instance itself is spelled in no declaration (#700).
    """
    kind, name = cycle[0]
    table = struct_table if kind == "struct" else enum_table

    er.emit(reporter, er.ERR.CE2095, table.spans.get(name),
            filename=table.files.get(name),
            name=display_type_name(name), chain=_format_chain(cycle))
