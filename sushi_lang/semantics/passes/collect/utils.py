"""Shared utilities for collection passes."""

from __future__ import annotations
from dataclasses import dataclass
from typing import (
    Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, TYPE_CHECKING)

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import BoundedTypeParam, Param
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import Param as CollectedParam


class TypeNameTable(Protocol):
    """What every type table answers: who holds a name, where, and in which unit."""
    by_name: Dict[str, Any]
    spans: Dict[str, Optional[Span]]
    files: Dict[str, Optional[str]]


@dataclass(frozen=True)
class TakenName:
    """One table a name may already be taken in, and the diagnostic that says so."""
    table: 'TypeNameTable'
    code: Any
    what: str = "first defined here"


def type_name_rules(kind: str, *, structs: 'TypeNameTable',
                    generic_structs: 'TypeNameTable', enums: 'TypeNameTable',
                    generic_enums: 'TypeNameTable') -> tuple[TakenName, ...]:
    """Every table a new struct or enum name may already be taken in (#901).

    A struct and an enum share one type name. Both collectors read this one list, so a
    declaration of either kind meets the other kind's tables in every collection order.
    """
    from sushi_lang.internals.errors import ERR

    same, other = (("struct", "enum") if kind == "struct" else ("enum", "struct"))
    duplicate = ERR.CE0004 if kind == "struct" else ERR.CE2046
    own, own_generic = ((structs, generic_structs) if kind == "struct"
                        else (enums, generic_enums))
    theirs, theirs_generic = ((enums, generic_enums) if kind == "struct"
                              else (structs, generic_structs))
    article = "an" if other == "enum" else "a"
    return (
        TakenName(own, duplicate),
        TakenName(own_generic, duplicate, f"first defined here, as a generic {same}"),
        TakenName(theirs, ERR.CE0006, f"already defined as {article} {other} here"),
        TakenName(theirs_generic, ERR.CE0006,
                  f"already defined as a generic {other} here"),
    )


def extract_type_param_names(type_params_raw: Optional[List]) -> Optional[List[str]]:
    """Extract type parameter names from AST type_params."""
    if type_params_raw is None:
        return None

    if not isinstance(type_params_raw, list) or len(type_params_raw) == 0:
        return None

    names = []
    for tp in type_params_raw:
        if isinstance(tp, str):
            names.append(tp)
        elif isinstance(tp, BoundedTypeParam):
            names.append(tp.name)
        else:
            continue

    return names if names else None


def param_from_node(p: Param, idx: int) -> 'CollectedParam':
    """Convert AST parameter node to the collected Param dataclass."""
    from .functions import Param as CollectedParam  # avoids a circular import

    pname = p.name
    pty: Optional[Type] = p.ty
    pname_span: Optional[Span] = p.name_span
    ptype_span: Optional[Span] = p.type_span

    if not isinstance(pname, str):
        pname = str(pname) if pname is not None else f"_p{idx}"

    return CollectedParam(
        name=pname,
        ty=pty,
        name_span=pname_span,
        type_span=ptype_span,
        index=idx,
        is_variadic=bool(p.is_variadic),
        is_pack=bool(p.is_pack),
        is_nom=bool(p.is_nom),
    )


def note_first_declaration(builder: Any, spans: dict, name: str,
                           what: str = "first defined here",
                           files: Optional[dict] = None) -> Any:
    """Attach the ORIGINAL declaration's location to a duplicate-declaration error.

    `files` answers which unit that declaration was in. The current unit is not the
    answer: the note points at a table entry, and the entry may have been made while
    another unit was being collected (#473).
    """
    prev = spans.get(name)
    if prev is not None:
        return builder.note_at(what, prev, files.get(name) if files else None)
    return builder.note("defined by the compiler")


_OTHER_TYPE_KIND = {"struct": "enum", "enum": "struct"}


def reject_duplicate_type_name(
    reporter, kind: str, name: str, name_span: Optional[Span],
    rules: Sequence[TakenName],
    library_clash: Optional[Callable[[str, Optional[Span]], bool]] = None,
) -> bool:
    """A TYPE name is one per program: refuse the second declaration of it.

    One rule for an enum and for a struct alike. The tables are asked in the order the
    caller lists them, and the first that holds the name answers with its own code and
    its own note. `library_clash` is the CE3011 arm: a source library's PRIVATE type
    took the name, which is a different fault from the plain duplicate, and it is asked
    once, before the arms, exactly when some table holds the name.
    """
    from sushi_lang.internals import errors as er

    if not any(name in rule.table.by_name for rule in rules):
        return False

    if library_clash is not None and library_clash(name, name_span):
        return True

    for rule in rules:
        if name in rule.table.by_name:
            note_first_declaration(
                er.emit_with(reporter, rule.code, name_span, name=name, kind=kind,
                             other=_OTHER_TYPE_KIND[kind]),
                rule.table.spans, name, what=rule.what, files=rule.table.files,
            ).emit()
            return True
    return False


def reject_reference_in(reporter, ty: Optional[Type], span: Optional[Span],
                        code) -> bool:
    """Reject a reference type in a position that has no semantics for one (R4)."""
    from sushi_lang.internals import errors as er
    from sushi_lang.semantics.generics.type_display import display_type
    from sushi_lang.semantics.type_predicates import contains_reference

    if not contains_reference(ty):
        return False
    er.emit(reporter, code, span, ty=display_type(ty))
    return True


def reject_try_in_body(reporter, body: Any, context: str) -> None:
    """Reject every `??` in an extension or perk method body (CE0131, #398).

    These bodies return a bare value (CE2091), so a `??` has nothing to
    propagate into. The rule is structural, so the collect pass owns it: the walk sees
    the DECLARATION, fires once per occurrence, and covers a template nobody
    instantiates. The walk SKIPS lambda subtrees: a lambda has
    its own Result channel, so a `??` inside one is legal (#399).
    """
    from sushi_lang.internals import errors as er
    from sushi_lang.semantics.ast import Lambda, Node, TryExpr
    from sushi_lang.semantics.ast_walk import walk_nodes

    def refuse_a_try(node: Node) -> bool:
        if isinstance(node, Lambda):
            return False
        if isinstance(node, TryExpr):
            er.emit_with(reporter, er.ERR.CE0131,
                         node.loc, context=context) \
                .help("handle the Result in the body: match on it, or use "
                      ".realise(default)").emit()
        return True

    walk_nodes(body, refuse_a_try)


def reject_self_in_body(reporter, body: Any, name: str) -> None:
    """Reject every mention of `self` in a STATIC method body (CE0134, #542).

    The second position of one fault: a static is called on the type name, so there is
    no receiver to read. Structural, so the collect pass owns it, and a lambda inside
    the body is walked too -- it has no receiver either.
    """
    from sushi_lang.internals import errors as er
    from sushi_lang.semantics.ast import Name, Node
    from sushi_lang.semantics.ast_walk import walk_nodes

    def refuse_a_self(node: Node) -> bool:
        if isinstance(node, Name) and node.id == "self":
            er.emit_with(reporter, er.ERR.CE0134,
                         node.loc, name=name) \
                .help("a static has no receiver: take what it needs as a "
                      "parameter, or drop the `static` marker").emit()
        return True

    walk_nodes(body, refuse_a_self)


def types_to_walk(table: Any, only: Optional[Iterable[str]] = None) -> List[Any]:
    """The entries of one type table a pass run covers.

    `only` is the names the run is narrowed to, and `None` is the whole table. One name
    list serves both tables: a type name is one per program, so each table answers with
    the names it holds and steps over the rest. The late-interning seam narrows its
    resolve and derive runs this way, because the passes already walked the table whole
    and an instance interned late is the only thing that changed (#676).
    """
    if only is None:
        return list(table.by_name.values())
    return [table.by_name[name] for name in only if name in table.by_name]
