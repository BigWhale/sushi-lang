"""Static methods: a receiver-less method called on a type name (#542).

One namespace sits behind a type's dot (ruling Q1): a name there is a MEMBER of that
type -- a variant, or a static method -- and never both. A local of the same name wins
first, which is #296's rule and not a new one.

This module holds what every pass needs to ASK, so no pass answers it a second way:
which names are type names, and which type a receiver denotes. What a static RESOLVES
to is `passes/types/calls/statics.py`, because that answer needs the method tables.
"""
from __future__ import annotations

from typing import AbstractSet, Optional

from sushi_lang.semantics.typesys import BuiltinType

# Every primitive a receiver position may name. `~` is excluded: it is the blank type
# and has no methods to hang a static on.
BUILTIN_TYPE_NAMES: frozenset[str] = frozenset(
    ty.value for ty in BuiltinType if ty is not BuiltinType.BLANK)


def builtin_type_named(name: str) -> Optional[BuiltinType]:
    """The primitive a name denotes, or None."""
    return BuiltinType(name) if name in BUILTIN_TYPE_NAMES else None


def names_a_type(name: str, *,
                 structs: AbstractSet[str] = frozenset(),
                 enums: AbstractSet[str] = frozenset(),
                 generic_structs: AbstractSet[str] = frozenset(),
                 generic_enums: AbstractSet[str] = frozenset()) -> bool:
    """Whether `name` denotes a TYPE, of any kind a static may be declared on.

    Local-wins is the CALLER's to apply: this asks about the name alone, and a pass
    that has a scope asks whether a local holds the name before it asks this.
    """
    return (name in BUILTIN_TYPE_NAMES
            or name in structs
            or name in enums
            or name in generic_structs
            or name in generic_enums)


# The BUILT-IN statics (ruling R3). They are static methods under the same rule as a
# user's, and they are named here so the general path can DEFER to them: each is
# emitted by its container's own narrow handler, which no user declaration reaches, so
# refusing one as "no such static" would break every `List.new()` in the stdlib.
#
# One table, not a string test per pass. Converging the HANDLERS is a separate change:
# a container static has no `ExtendDef` to resolve, so there is nothing yet to converge
# onto, and #553's lesson says the narrow paths stay until a test proves otherwise.
BUILTIN_STATICS: dict[str, frozenset[str]] = {
    "List": frozenset({"new", "with_capacity"}),
    "HashMap": frozenset({"new"}),
    "Own": frozenset({"alloc"}),
    "f64": frozenset({"from_bits"}),
    "f32": frozenset({"from_bits"}),
}


def is_builtin_static(type_name: Optional[str], method: str) -> bool:
    """Whether `<type_name>.<method>()` is one of the built-in statics."""
    if type_name is None:
        return False
    return method in BUILTIN_STATICS.get(type_name, frozenset())


def static_template(generic_extensions, base: str, method: str):
    """The generic-target TEMPLATE that declares `<base>.<method>` as a static, or None.

    A concrete-target static (`extend Box@(i32) static ...`) is not a template: it names
    no type parameter in its signature, so an argument can solve nothing from it and the
    stamp is its only source, as before #573.
    """
    for declaration in generic_extensions.declarations(base, method):
        if getattr(declaration, "is_static", False) and not declaration.target_key:
            return declaration
    return None


def solve_target_type_args(template, arg_types, stamped_args):
    """A generic static's TARGET type arguments: the arguments first, the stamp second.

    ONE resolution for both passes (#573, MIG.md R5). Every target type parameter a
    parameter names is unified from the argument in that position, exactly as a generic
    free function solves its own. What is still unsolved reads the propagation stamp at
    the binding site, positionally. Returns `(type_args, unsolved)`: the tuple and `()`
    when every parameter was reached, else `None` and the names neither source reached,
    in declaration order. An argument whose type is not known yet (`None`) solves
    nothing, and a stamp whose arity does not match the template answers nothing.
    """
    from sushi_lang.semantics.generics.unify import unify_types

    solved: dict = {}
    for param, arg_type in zip(template.params, arg_types, strict=False):
        if param.ty is None or arg_type is None:
            continue
        unify_types(param.ty, arg_type, solved)

    names = [p.name if hasattr(p, "name") else str(p) for p in template.type_params]
    if stamped_args is not None and len(stamped_args) == len(names):
        for name, stamped in zip(names, stamped_args, strict=True):
            solved.setdefault(name, stamped)

    unsolved = tuple(name for name in names if name not in solved)
    if unsolved:
        return None, unsolved
    return tuple(solved[name] for name in names), ()
