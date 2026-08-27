"""The fence over every PUBLIC signature: one walk, one predicate per rule.

A public declaration is a promise, and a signature is where it is kept. Two rules police
it, and both ask the same two questions -- is this declaration public, and does this type
belong here -- so they share the walk and differ only in the predicate.

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
from sushi_lang.semantics.ast_walk import TypeSite, signature_types
from sushi_lang.semantics.type_predicates import contains_foreign_ptr

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


# What a diagnostic calls each declaration. `declarations()` words the AST walk; a user
# reading an error wants the thing they wrote, so an extension is a method here.
_KIND_WORD = {
    "extension": "extension method",
}


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


def _is_public(validator: 'TypeValidator', site: TypeSite) -> bool:
    """Whether the declaration owning this position is part of the unit's API.

    Three answers, one per group of `semantics/visibility.py`. A declaration that carries
    its own marker reads it. An extension and a perk implementation read their TARGET
    type's, which is Ruling 2. A perk's own method reads the perk's.
    """
    if site.kind in ("extension", "perk implementation") or isinstance(site.decl, ExtendWithDef):
        return _type_is_public(validator, getattr(site.decl, "target_type", None))
    return bool(getattr(site.decl, "is_public", True))


def _type_is_public(validator: 'TypeValidator', ty: Optional[Any]) -> bool:
    """Whether a named type may be seen outside its unit.

    A type with no record was never declared in source -- a builtin, a monomorphized
    instance, a lifted environment -- and those are public by absence.
    """
    name = getattr(ty, "name", None)
    if not isinstance(name, str):
        return True
    table = validator.visibility
    for kind in ("struct", "enum"):
        origin = table.origin(kind, name)
        if origin is not None:
            return origin.is_public
    return True


def check_public_signatures(validator: 'TypeValidator', program: 'Program') -> None:
    """Run every public-signature rule over one unit."""
    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    for site in signature_types(program):
        if site.ty is None or not _is_public(validator, site):
            continue
        if (site.kind in _PTR_RULE_KINDS and site.position in _PTR_RULE_POSITIONS
                and contains_foreign_ptr(site.ty, structs, enums)):
            er.emit(validator.reporter, er.ERR.CE5008, site.span,
                    kind=_KIND_WORD.get(site.kind, site.kind),
                    name=_declared_name(site))
