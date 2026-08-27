"""The fence over every PUBLIC signature: one walk, one predicate per rule.

A public declaration is a promise, and a signature is where it is kept. Two rules police
it, and both ask the same two questions -- is this declaration public, and does this type
belong here -- so they share the walk and differ only in the predicate.

The leak rule (CE3009, CE3010) is the second, and it brings its own pair of sets: it
includes exactly the two positions the `ptr` rule excludes, because a public struct's
field and a public enum's payload are part of what the declaration promises.

CE5008 was the first, and it read `ret` and `params` of a `public fn` and nothing else. It
therefore missed a `public fn`'s error arm, every public GENERIC (the typecheck runner
skips one), every extension and every perk method -- four holes, each of which compiled
clean. The fence runs over `signature_types()` now, so a position cannot be missing from
one rule and present in another.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import ExtendWithDef
from sushi_lang.semantics.ast_walk import (
    TypeSite, signature_constraints, signature_types)
from sushi_lang.semantics.type_predicates import contains_foreign_ptr
from .visibility import name_is_contested

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program
    from . import TypeValidator


# The `ptr` rule polices what crosses a CALL boundary, so it reads two facts. A struct
# field and an enum payload are exempt on purpose: the wrapper-struct pattern is the
# sanctioned way to carry a foreign handle across a unit, and CE5008's own text says so.
# The RECEIVER is exempt for the same reason -- an extension on a struct that already
# carries a `ptr` field exposes nothing the struct did not, and refusing it would make a
# wrapper struct unextendable. A rule with a different answer brings its own sets rather
# than widening these.
_PTR_RULE_KINDS = frozenset({"function", "extension", "perk method"})
_PTR_RULE_POSITIONS = frozenset({"return", "error", "parameter"})


# The leak rule polices what a public declaration HANDS OUT, so it reads a wider set than
# the `ptr` rule and includes exactly the two positions that one excludes: a public
# struct's field and a public enum's variant payload are part of the promise (decision 2
# of `docs/design/visibility.md`). The RECEIVER is absent because it IS the gate -- an
# extension is as visible as its target type, so asking whether the target is private
# after asking whether it is public would answer itself.
_LEAK_RULE_KINDS = frozenset({
    "constant", "struct", "enum", "function", "extension", "perk method",
    "perk implementation",
})
_LEAK_RULE_POSITIONS = frozenset({
    "type", "return", "error", "parameter", "field", "variant",
})


# What a diagnostic calls each declaration. `declarations()` words the AST walk; a user
# reading an error wants the thing they wrote, so an extension is a method here.
_KIND_WORD = {
    "extension": "extension method",
}

# A position that belongs to an inner node needs the inner word, because that is what the
# name beside it belongs to: `_declared_name` returns the FIELD's name, so "struct" alone
# would read "public struct 'at'".
_POSITION_WORD = {
    "field": "struct field",
    "variant": "enum variant",
}


def _leak_word(site: TypeSite) -> str:
    """The word the leak diagnostic uses for this position."""
    return _POSITION_WORD.get(site.position,
                              _KIND_WORD.get(site.kind, site.kind))


def _declared_name(site: TypeSite) -> str:
    """The name a diagnostic uses: the inner declaration's, not its parent's."""
    for candidate in (site.at, site.decl):
        name = getattr(candidate, "name", None)
        if isinstance(name, str):
            return name
        perk = getattr(candidate, "perk_name", None)
        if isinstance(perk, str):
            return perk
    return "<anonymous>"


def _declared_public(validator: 'TypeValidator', site: TypeSite) -> Optional[bool]:
    """Whether the declaration owning this position is part of the unit's API.

    Three answers, one per group of `semantics/visibility.py`. A declaration that carries
    its own marker reads it. An extension and a perk implementation read their TARGET
    type's, which is Ruling 2. A perk's own method reads the perk's.

    None is a fourth answer, and it belongs to the target alone: nothing in this program's
    source declares the type, so there is no marker to inherit. The two rules read that
    differently, which is why it is not folded into a bool here.
    """
    if site.kind in ("extension", "perk implementation") or isinstance(site.decl, ExtendWithDef):
        return _type_visibility(validator, getattr(site.decl, "target_type", None))
    return bool(getattr(site.decl, "is_public", True))


def _type_visibility(validator: 'TypeValidator', ty: Optional[Any]) -> Optional[bool]:
    """A named type's marker, or None when nothing declared it.

    A builtin, a monomorphized instance and a lifted environment all answer None: they are
    nameable everywhere, and none of them carries a source marker.
    """
    name = getattr(ty, "name", None)
    if not isinstance(name, str):
        return None
    table = validator.visibility
    for kind in ("struct", "enum"):
        origin = table.origin(kind, name)
        if origin is not None:
            return origin.is_public
    return None


def _private_origin(validator: 'TypeValidator', kind: str,
                    name: str) -> Optional[Any]:
    """The record for `name`, when it says the name may not leave its unit."""
    origin = validator.visibility.origin(kind, name)
    if origin is None or origin.is_public:
        return None
    # A name this unit declared and LOST reads somebody else's record. Reporting a leak
    # against a declaration this unit never wrote is the D2 cascade.
    if name_is_contested(validator, kind, name):
        return None
    return origin


def _leaked_type(validator: 'TypeValidator', ty: Any) -> Optional[Any]:
    """The private struct or enum this type hands out, or None."""
    found: list[Any] = []

    def is_private(name: str) -> bool:
        for kind in ("struct", "enum"):
            origin = _private_origin(validator, kind, name)
            if origin is not None:
                found.append(origin)
                return True
        return False

    from sushi_lang.semantics.type_predicates import first_private_name
    if first_private_name(ty, is_private) is None:
        return None
    return found[0]


def _note_declaration(diagnostic, origin) -> None:
    """Point at the declaration that refused, when it can say where it lives."""
    if origin.name_span is not None and origin.filename is not None:
        diagnostic.note("declared here, without `public`",
                        origin.name_span, origin.filename).emit()
        return
    diagnostic.emit()


def check_public_signatures(validator: 'TypeValidator', program: 'Program') -> None:
    """Run every public-signature rule over one unit.

    Not over a LIBRARY unit. Its signatures were fenced when the library was built, and at
    the consumer its templates carry whatever the consumer's call substituted -- a private
    type of the consumer's included -- so anything raised here would name code the
    consumer did not write.
    """
    if validator.in_library_unit:
        return

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    for site in signature_types(program):
        if site.ty is None:
            continue
        public = _declared_public(validator, site)
        # The `ptr` rule fences a target with no declaration: `extend i32 handle() ptr`
        # hands a foreign pointer across every unit boundary there is.
        if (public is not False and site.kind in _PTR_RULE_KINDS
                and site.position in _PTR_RULE_POSITIONS
                and contains_foreign_ptr(site.ty, structs, enums)):
            er.emit(validator.reporter, er.ERR.CE5008, site.span,
                    kind=_KIND_WORD.get(site.kind, site.kind),
                    name=_declared_name(site))
        # The leak rule does not. It measures a PROMISE, and an extension on a builtin
        # inherits no marker to promise with, so a single-unit file never notices the flip
        # -- which is what `docs/design/visibility.md` section 6 undertakes. The residue is
        # covered where it matters: a consumer that receives such a value cannot write the
        # type down, because the type funnel refuses the name.
        if (public is True and site.kind in _LEAK_RULE_KINDS
                and site.position in _LEAK_RULE_POSITIONS):
            origin = _leaked_type(validator, site.ty)
            if origin is not None:
                _note_declaration(er.emit_with(
                    validator.reporter, er.ERR.CE3009, site.span,
                    kind=_leak_word(site),
                    name=_declared_name(site), type=origin.name), origin)

    for constraint in signature_constraints(program):
        if not bool(getattr(constraint.decl, "is_public", True)):
            continue
        origin = _private_origin(validator, "perk", constraint.perk_name)
        if origin is not None:
            _note_declaration(er.emit_with(
                validator.reporter, er.ERR.CE3010, constraint.span,
                kind=_KIND_WORD.get(constraint.kind, constraint.kind),
                name=getattr(constraint.decl, "name", "<anonymous>"),
                perk=constraint.perk_name), origin)
