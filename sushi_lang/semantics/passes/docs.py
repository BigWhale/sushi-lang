"""The `docs` pass: check every doc block against the declaration beside it.

Runs after `collect` and before `externs`. It needs the AST and nothing else --
parameter names, the return type and the error arm are all on the declaration
already -- and it must run before `instantiate` and `monomorphize`, so a generic's
doc block is checked once rather than once per instantiation.

Every check here is ALWAYS ON, because every one of them finds a claim that
contradicts the declaration. Completeness is a matter of policy and belongs behind
`--warn-missing-docs`; documentation.md section 6 is the contract, and phase 5 owns
the flag. Library units are skipped by the caller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, List, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast_builder.declarations.docs import suggest_tag

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.ast import DocBlock, Program

# A doc block and the declaration it documents. The owner is None for the unit block,
# which documents no declaration.
Documented = Tuple['DocBlock', Optional[object]]

# `- Returns:` and `- Errors:` are singletons by documentation.md section 3.
_SINGLETON_TAGS = ("returns", "errors")


def check_docs(reporter: 'Reporter', program: 'Program') -> None:
    """Check one unit's doc blocks."""
    for doc, owner in _documented(program):
        _check_tags(reporter, doc, owner)
    _check_positions(reporter, program)


def _bodied(program: 'Program') -> List:
    """Every declaration with a body, which is every declaration that can hold two blocks."""
    return [*program.functions, *program.extensions, *program.generic_extensions,
            *[method for impl in program.perk_impls for method in impl.methods]]


def _documented(program: 'Program') -> Iterator[Documented]:
    """Every block that IS attached to something, with what it is attached to.

    An orphan is deliberately absent: it already carries its own diagnostic, and
    checking its tags as well would say two things about one mistake.
    """
    def pair(owner) -> Iterator[Documented]:
        if owner is not None and getattr(owner, "doc", None) is not None:
            yield owner.doc, owner

    if program.doc is not None:
        yield program.doc, None

    for const in program.constants:
        yield from pair(const)
    for struct in program.structs:
        yield from pair(struct)
        for field in struct.fields:
            yield from pair(field)
    for enum in program.enums:
        yield from pair(enum)
        for variant in enum.variants:
            yield from pair(variant)
    for perk in program.perks:
        yield from pair(perk)
        for method in perk.methods:
            yield from pair(method)
    for impl in program.perk_impls:
        yield from pair(impl)
    for block in program.externals:
        yield from pair(block)
        for decl in block.decls:
            yield from pair(decl)

    for decl in _bodied(program):
        yield from pair(decl)
        # A declaration documented from above AND from inside carries two blocks;
        # the lifted one is `decl.doc` when there is no block above it.
        body_doc = getattr(decl.body, "doc", None)
        if body_doc is not None and body_doc is not decl.doc:
            yield body_doc, decl


def _declared_parameters(owner) -> Optional[set]:
    """The parameter names of a callable, or None when the owner declares none.

    None and an empty set are different answers: a callable with no parameters makes
    every `- Parameter` tag wrong, while a struct has no opinion about one.
    """
    params = getattr(owner, "params", None) if owner is not None else None
    return None if params is None else {param.name for param in params}


def _check_tags(reporter: 'Reporter', doc: 'DocBlock', owner) -> None:
    declared = _declared_parameters(owner)
    name = getattr(owner, "name", "") if owner is not None else ""

    first_for_parameter: dict = {}
    first_singleton: dict = {}

    for tag in doc.tags:
        if tag.kind == "unknown":
            builder = er.emit_with(reporter, er.ERR.CE7004, tag.loc, word=tag.word)
            suggestion = suggest_tag(tag.word)
            if suggestion is not None:
                builder.help(f"did you mean `- {suggestion}:`?")
            continue

        if tag.kind == "parameter":
            written = tag.name or ""
            first = first_for_parameter.get(written)
            if first is not None:
                er.emit_with(reporter, er.ERR.CE7002, tag.loc, name=written).note(
                    f"'{written}' is documented here as well", first.loc)
                continue
            first_for_parameter[written] = tag
            if declared is not None and written not in declared:
                # The name span, so the note's caret lands on the callable's name
                # rather than collapsing over the whole declaration.
                where = getattr(owner, "name_span", None) or getattr(owner, "loc", None)
                er.emit_with(reporter, er.ERR.CE7001, tag.loc,
                             name=written, callable=name).note(
                    f"'{name}' is declared here", where)
            continue

        if tag.kind in _SINGLETON_TAGS:
            first = first_singleton.get(tag.kind)
            if first is not None:
                er.emit_with(reporter, er.ERR.CE7003, tag.loc, tag=tag.word).note(
                    "the first one is here", first.loc)
                continue
            first_singleton[tag.kind] = tag


def _check_positions(reporter: 'Reporter', program: 'Program') -> None:
    for doc in program.orphan_docs:
        if doc.orphan_reason == "in-body":
            er.emit(reporter, er.ERR.CE7005, doc.loc)
        else:
            er.emit_with(reporter, er.ERR.CW7001, doc.loc).help(
                "a block documents the declaration on the next line; a blank line or "
                "a comment between the two breaks the attachment")

    for decl in _bodied(program):
        body_doc = getattr(decl.body, "doc", None)
        if decl.doc is not None and body_doc is not None and body_doc is not decl.doc:
            er.emit_with(reporter, er.ERR.CE7006, body_doc.loc,
                         name=decl.name).note(
                "the other block is here", decl.doc.loc)
