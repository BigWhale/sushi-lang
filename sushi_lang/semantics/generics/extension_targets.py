"""What the `@(...)` arguments of an extension target MEAN (#393).

`extend Box@(T)` and `extend Box@(Point)` are spelled the same way -- both arguments parse as
an `UnknownType` -- and they mean opposite things. The first names a type PARAMETER and
applies to every instantiation of `Box`; the second is a CONSTRAINT and applies to
`Box<Point>` and to nothing else, exactly as a perk implementation on the same target
already did.

The question is answered ONCE, in the collect pass, and `DeclaredTypeNamer` is the one
predicate that says which bare names are declared (#653): the four type tables through
`statics.names_a_type`, plus the perk table -- a perk is not a type, but a declared name
never binds a fresh parameter, so `extend Box@(Show)` reads as a constraint and `Show`
then fails as a type where every other type position fails it. Two collectors used to
answer from two different sets, so one spelling meant a template on the perk path and
a constraint on the extension path. The answer is carried on the declaration (`ExtendDef.target_shape`) and
on its collected signature (`GenericExtensionMethod`), so the instantiate and monomorphize passes read it
rather than deciding again -- two decisions from two sets of visible types could disagree,
and the disagreement would be silent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Protocol, Tuple

from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.statics import names_a_type
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    StructType,
    Type,
    UnknownType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter


# A target the concrete extension table can key on. A named type is nominal, so the
# table holds the type object itself -- and a name that resolved to nothing, a
# reference, or a `@(...)` reference is not one of these and belongs to another
# collector. ONE tuple: four readers asked the same question with four copies of it.
CONCRETE_EXTENSION_TARGETS = (
    BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)


class NameTable(Protocol):
    """What the predicate reads of a symbol table: the names it holds."""

    @property
    def by_name(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class DeclaredTypeNamer:
    """Does this bare name in a target's argument position name a TYPE? (#653)

    The tables are read at call time, so a name collected later is seen. Every
    classifier call hands in one of these and nothing else; the gate is
    `tests/unit/test_declared_type_predicate_is_one.py`.
    """

    structs: NameTable
    enums: NameTable
    generic_structs: NameTable
    generic_enums: NameTable
    perks: NameTable

    def __call__(self, name: str) -> bool:
        return (names_a_type(name,
                             structs=self.structs.by_name.keys(),
                             enums=self.enums.by_name.keys(),
                             generic_structs=self.generic_structs.by_name.keys(),
                             generic_enums=self.generic_enums.by_name.keys())
                or name in self.perks.by_name)

    def names_a_whole_type(self, name: str) -> bool:
        """Does the bare name denote a type ON ITS OWN, arguments and all? (#698)

        The question above is "does this name bind a fresh parameter", and every
        declared name answers no to that. It is not the same question: `Cage` is a
        generic and needs its own arguments, and `Show` is a perk and is no type at
        all, so `Box@(Cage)` and `Box@(Show)` name a constraint on an instantiation
        that can never exist.
        """
        return names_a_type(name,
                            structs=self.structs.by_name.keys(),
                            enums=self.enums.by_name.keys())

    def names_a_generic(self, name: str) -> bool:
        """Does the bare name denote a generic type, which a `@(...)` target needs?"""
        return (name in self.generic_structs.by_name
                or name in self.generic_enums.by_name)


@dataclass(frozen=True)
class ExtensionTarget:
    """The classified target of one `extend <Base>@(...)` declaration."""

    base_name: str
    args: Tuple[Type, ...]
    param_names: Tuple[str, ...]   # the arguments that name a type parameter
    target_key: str                # the instantiation it constrains, "" for a template

    @property
    def is_concrete(self) -> bool:
        """Every argument is a type, so the target names one instantiation."""
        return self.target_key != ""

    @property
    def is_mixed(self) -> bool:
        """Some arguments are types and some are parameters -- rejected (CE2098)."""
        return bool(self.param_names) and len(self.param_names) != len(self.args)


def instantiation_key(base_name: str, type_args: Tuple[Type, ...]) -> str:
    """The interned name of one instantiation, which is what a concrete target matches."""
    return interned_name(base_name, type_args)


def classify_extension_target(
    target: GenericTypeRef,
    is_declared_type: Callable[[str], bool],
) -> ExtensionTarget:
    """Read a target's arguments as constraints, as parameter names, or as a mix."""
    args = tuple(target.type_args)
    param_names = tuple(
        str(arg) for arg in args if not _names_a_type(arg, is_declared_type)
    )
    concrete = not param_names
    return ExtensionTarget(
        base_name=target.base_name,
        args=args,
        param_names=param_names,
        target_key=instantiation_key(target.base_name, args) if concrete else "",
    )


def reject_unwritable_target(
    reporter,
    shape: ExtensionTarget,
    namer: DeclaredTypeNamer,
    span,
) -> bool:
    """CE2001 for a `@(...)` target that names no type it could apply to (#698).

    Both paths ask here, so one header reads one way. Every argument names a type on
    its own, and the base names the generic they instantiate. A declared name that is
    neither -- a generic without its arguments, a perk -- binds no parameter either, so
    it constrains an instantiation that can never exist.

    The BASE is asked of every target, template and concrete alike (#728): a template
    on a base that names no generic is as dead as a concrete one, because nothing can
    write the target either way. The ARGUMENTS are asked of a concrete target only,
    because a bare undeclared argument in a template IS the parameter it binds, which
    is the whole of #393. The COUNT is asked of every target too (CE2062, #796).

    What this does NOT ask is whether the instantiation the target names was ever
    written. A target is a CONSTRAINT and not a use: an implementation nothing reaches
    is dead code, exactly as an unused extension is.
    """
    from sushi_lang.internals import errors as er

    if not namer.names_a_generic(shape.base_name):
        er.emit(reporter, er.ERR.CE2001, span, name=shape.base_name)
        return True

    if _reject_target_arity(reporter, shape, namer, span):
        return True

    if not shape.is_concrete:
        return False

    refused = False
    for arg in shape.args:
        if not isinstance(arg, UnknownType) or namer.names_a_whole_type(arg.name):
            continue
        help_line = ("write its type arguments, as in '{0}@(i32)'".format(arg.name)
                     if namer.names_a_generic(arg.name)
                     else f"'{arg.name}' is a perk, and a target argument names a type")
        er.emit_with(reporter, er.ERR.CE2001, span, name=arg.name) \
            .help(help_line).emit()
        refused = True
    return refused


def _reject_target_arity(reporter, shape: ExtensionTarget, namer: DeclaredTypeNamer,
                         span) -> bool:
    """CE2062 for a target whose `@(...)` list is not the count its base declares (#796).

    A template and a concrete target alike: `extend Box@(T, U)` binds a parameter the
    type does not have, and `extend Box@(i32, i32)` constrains an instantiation that
    cannot exist.
    """
    from sushi_lang.semantics.generics.explicit_type_args import reject_type_arg_arity

    for table in (namer.generic_structs, namer.generic_enums):
        generic = table.by_name.get(shape.base_name)
        if generic is None:
            continue
        return reject_type_arg_arity(
            reporter, shape.base_name, generic, len(shape.args), span,
            declared_at=getattr(table, "spans", {}).get(shape.base_name),
            declared_in=getattr(table, "files", {}).get(shape.base_name))
    return False


# The synthetic base name under which array-target templates live in the
# GenericExtensionTable. A `$` cannot appear in a written type name, so the key
# collides with nothing a program declares.
ARRAY_BASE_KEY = "$array"


def classify_array_extension_target(
    element: Type,
    is_declared_type: Callable[[str], bool],
) -> Optional[ExtensionTarget]:
    """Read a dynamic-array target's ELEMENT position (ruling 3 of the UFCS epic).

    A bare undeclared name binds a type parameter (`extend T[]` applies to every
    element type); a declared or built-in name is concrete (`extend i32[]`). Anything
    else -- a generic instantiation, an array -- returns None, which the collect pass
    reports as CE2101.
    """
    if isinstance(element, TypeParameter):
        return ExtensionTarget(base_name=ARRAY_BASE_KEY, args=(element,),
                               param_names=(element.name,), target_key="")
    if isinstance(element, UnknownType):
        if is_declared_type(element.name):
            return ExtensionTarget(
                base_name=ARRAY_BASE_KEY, args=(element,), param_names=(),
                target_key=instantiation_key(ARRAY_BASE_KEY, (element,)))
        return ExtensionTarget(base_name=ARRAY_BASE_KEY, args=(element,),
                               param_names=(element.name,), target_key="")
    if isinstance(element, (BuiltinType, StructType, EnumType)):
        return ExtensionTarget(
            base_name=ARRAY_BASE_KEY, args=(element,), param_names=(),
            target_key=instantiation_key(ARRAY_BASE_KEY, (element,)))
    return None


def target_shape_of(ext) -> Optional[ExtensionTarget]:
    """The shape the collect pass stamped on a declaration, if it stamped one."""
    return getattr(ext, "target_shape", None)


def _names_a_type(arg: Type, is_declared_type: Callable[[str], bool]) -> bool:
    """Whether one argument names a type rather than a type parameter.

    Only a bare name is ambiguous. `i32`, `List@(i32)` and `i32[]` are already types, and a
    `TypeParameter` has already been read as a parameter somewhere upstream.
    """
    if isinstance(arg, TypeParameter):
        return False
    if isinstance(arg, UnknownType):
        return is_declared_type(arg.name)
    return True
