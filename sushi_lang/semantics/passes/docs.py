"""The `docs` pass: check every doc block against the declaration beside it.

Runs after `collect` and before `externs`. It needs the AST and nothing else --
parameter names, the return type and the error arm are all on the declaration
already -- and it must run before `instantiate` and `monomorphize`, so a generic's
doc block is checked once rather than once per instantiation.

The module has two entry points, one per side of documentation.md section 6's split.
`check_docs` is ALWAYS ON, because every check in it finds a claim that CONTRADICTS the
declaration. `check_missing_docs` reports what a block OMITS, which is a matter of
policy, and the CALLER runs it only under `--warn-missing-docs`. The pass takes no
policy flag of its own: that is what keeps the always-on side unable to drift behind a
flag. Library units are skipped by the caller, both ways.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, List, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast_builder.declarations.docs import suggest_tag
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.ast import DocBlock, Program

# A doc block and the declaration it documents. The owner is None for the unit block,
# which documents no declaration.
Documented = Tuple['DocBlock', Optional[object]]

# A declaration, and the word a diagnostic calls its kind by.
Declaration = Tuple[str, object]

# `- Returns:` and `- Errors:` are singletons by documentation.md section 3.
_SINGLETON_TAGS = ("returns", "errors")


def check_docs(reporter: 'Reporter', program: 'Program') -> None:
    """Check one unit's doc blocks."""
    for doc, owner in documented(program):
        _check_tags(reporter, doc, owner)
        _check_examples(reporter, doc)
    _check_positions(reporter, program)


def _bodied_kinds(program: 'Program') -> Iterator[Declaration]:
    """Every declaration with a body, with the word a diagnostic calls it by.

    A body is what lets a declaration hold two blocks, one above it and one first
    inside it, and it is why these come last in both walks.
    """
    for func in program.functions:
        yield "function", func
    for extension in [*program.extensions, *program.generic_extensions]:
        yield "extension", extension
    for impl in program.perk_impls:
        for method in impl.methods:
            yield "perk method", method


def _bodied(program: 'Program') -> List:
    """Every declaration with a body, in the one order both walks use."""
    return [node for _kind, node in _bodied_kinds(program)]


def declarations(program: 'Program') -> Iterator[Declaration]:
    """Every declaration of one unit, block or none, with the word for its kind.

    ONE walk over the AST (documentation.md S10, R34). `documented()` filters this, and
    `check_missing_docs` asks each yield whether it carries a block, so the two can
    never disagree about what a unit declares. Two walks would drift.

    The unit block is not here: it documents no declaration, and `Program` is not one.
    Nor is a body-first block, which the builders lift onto the declaration around it.

    The ORDER is fixed. `tests/docs_sweep.py` numbers its generated `doc_example_<n>`
    helpers from it, so a rearrangement renames every one of them.
    """
    for const in program.constants:
        yield "constant", const
    for struct in program.structs:
        yield "struct", struct
        for field in struct.fields:
            yield "field", field
    for enum in program.enums:
        yield "enum", enum
        for variant in enum.variants:
            yield "variant", variant
    for perk in program.perks:
        yield "perk", perk
        for method in perk.methods:
            yield "perk method", method
    for impl in program.perk_impls:
        yield "perk implementation", impl
    for block in program.externals:
        yield "external block", block
        for decl in block.decls:
            yield "external declaration", decl
    yield from _bodied_kinds(program)


def documented(program: 'Program') -> Iterator[Documented]:
    """Every block that IS attached to something, with what it is attached to.

    An orphan is deliberately absent: it already carries its own diagnostic, and
    checking its tags as well would say two things about one mistake.

    Public because `tests/docs_sweep.py` walks a unit the same way (documentation.md
    S10, R22). One walk, so the doc-test runner cannot see a block the pass does not.
    """
    if program.doc is not None:
        yield program.doc, None

    for _kind, owner in declarations(program):
        doc = getattr(owner, "doc", None)
        if doc is not None:
            yield doc, owner
        # A declaration documented from above AND from inside carries two blocks;
        # the lifted one is `owner.doc` when there is no block above it. Only a
        # bodied declaration can, and the walk yields those last.
        body = getattr(owner, "body", None)
        body_doc = getattr(body, "doc", None) if body is not None else None
        if body_doc is not None and body_doc is not doc:
            yield body_doc, owner


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


def _check_examples(reporter: 'Reporter', doc: 'DocBlock') -> None:
    """The two ways an `- Example:` can contradict itself (documentation.md S10, R17).

    Both are recorded by the parse and reported here, which is the split
    `DocBlock.orphan_reason` settled: the builder takes no Reporter, so it cannot
    diagnose, and dropping the defect there is the silent loss this feature removes.
    """
    for example in doc.examples:
        if example.defect == "no-fence":
            er.emit_with(reporter, er.ERR.CE7007, example.loc).help(
                "the tag introduces a fenced block; open one with ```sushi on the "
                "next line, or delete the tag")
        elif example.defect == "unterminated":
            er.emit_with(reporter, er.ERR.CE7008, example.loc).help(
                "close it with a run of the same character that is at least as long, "
                "before the block's own `:##`")


def _check_positions(reporter: 'Reporter', program: 'Program') -> None:
    # Every diagnostic here spans a WHOLE doc block, so it is reported location only:
    # a caret that covers the block marks everything and separates nothing, and the
    # header already carries the line and column.
    for doc in program.orphan_docs:
        if doc.orphan_reason == "in-body":
            er.emit_with(reporter, er.ERR.CE7005, doc.loc).location_only().help(
                "a block in a body documents the function around it, so it goes "
                "first; move it above the declaration to document something else")
        else:
            er.emit_with(reporter, er.ERR.CW7001, doc.loc).location_only().help(
                "a block documents the declaration on the next line; a blank line or "
                "a comment between the two breaks the attachment")

    for decl in _bodied(program):
        body_doc = getattr(decl.body, "doc", None)
        if decl.doc is not None and body_doc is not None and body_doc is not decl.doc:
            er.emit_with(reporter, er.ERR.CE7006, body_doc.loc,
                         name=decl.name).location_only().note(
                "the other block is here", decl.doc.loc)


# -- completeness, behind --warn-missing-docs -----------------------------------

# Where a diagnostic's caret goes, for a kind whose narrowest span is not `name_span`.
# A field and a variant carry no name span of their own, an implementation is named by
# the perk it implements, and a block by the namespace it binds.
_SPAN_FIELD = {
    "field": "loc",
    "variant": "loc",
    "perk implementation": "perk_name_span",
    "external block": "namespace_span",
}


def check_missing_docs(reporter: 'Reporter', program: 'Program') -> None:
    """Report what a unit's documentation OMITS (documentation.md section 6).

    Every diagnostic here is a warning, and the caller runs the whole function only
    under `--warn-missing-docs`. Completeness is policy: a codebase that has not been
    documented yet must not become a wall of warnings on the day the feature lands.

    R33 is the rule that keeps one omission to one diagnostic. CW7003, CW7004 and
    CW7005 presuppose a block, so a declaration with none collects CW7002 and stops.
    """
    if program.doc is None:
        # The one lint about something that is not there, so it has nothing to point at.
        er.emit_with(reporter, er.ERR.CW7006, None)

    for kind, node in declarations(program):
        doc = _attached_doc(node)
        if doc is None:
            if _wants_a_block(kind, node):
                er.emit_with(reporter, er.ERR.CW7002, _declaration_span(kind, node),
                             kind=kind, name=_declaration_name(kind, node))
            continue
        _check_completeness(reporter, doc, kind, node)


def _attached_doc(node) -> Optional['DocBlock']:
    """The block that documents a declaration, from above it or from inside its body."""
    doc = getattr(node, "doc", None)
    if doc is not None:
        return doc
    body = getattr(node, "body", None)
    return getattr(body, "doc", None) if body is not None else None


def _wants_a_block(kind: str, node) -> bool:
    """R30: the two exemptions, and the only place either one is named.

    `fn main()` is nobody's API, and a library cannot declare one at all (CE3501). An
    `unsafe external` block and the declarations in it carry `because "..."`, which
    acknowledges the contract that matters at that seam. Nothing else is exempt.
    """
    if kind == "function" and getattr(node, "name", "") == "main":
        return False
    return kind not in ("external block", "external declaration")


def _declaration_span(kind: str, node):
    return getattr(node, _SPAN_FIELD.get(kind, "name_span"), None)


def _declaration_name(kind: str, node) -> str:
    """What to call a declaration, for a reader who has to go and find it."""
    if kind == "external block":
        return getattr(node, "namespace", "")
    if kind == "perk implementation":
        target = node.target_type
        return f"{display_type(target) if target is not None else '?'} with {node.perk_name}"
    return getattr(node, "name", "")


def _check_completeness(reporter: 'Reporter', doc: 'DocBlock', kind: str, node) -> None:
    """The three lints that a block has to exist for (R33)."""
    name = getattr(node, "name", "")
    where = _declaration_span(kind, node)
    tagged = {tag.kind for tag in doc.tags}
    documented_params = {tag.name for tag in doc.tags if tag.kind == "parameter"}

    # `self` is never asked for: the builders strip the receiver and lift it onto the
    # declaration as `self_mode`, so it is not a parameter by the time this reads one.
    for param in getattr(node, "params", None) or []:
        if param.name not in documented_params:
            er.emit_with(reporter, er.ERR.CW7003, param.loc,
                         name=param.name, callable=name)

    # None means the declaration wrote no return type at all, which is an error of its
    # own; BLANK means it returns nothing there is anything to say about.
    ret = getattr(node, "ret", None)
    if ret is not None and ret is not BuiltinType.BLANK and "returns" not in tagged:
        er.emit_with(reporter, er.ERR.CW7004, where, name=name)

    # Only a FuncDef carries an error arm. `getattr` answers None for every other kind,
    # which is what makes this total with no per-kind branch.
    if getattr(node, "err_type", None) is not None and "errors" not in tagged:
        er.emit_with(reporter, er.ERR.CW7005, where, name=name)
