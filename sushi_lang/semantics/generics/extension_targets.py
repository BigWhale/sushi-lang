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

from sushi_lang.semantics.statics import names_a_type
from sushi_lang.semantics.typesys import Type, UnknownType
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter


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
    return f"{base_name}<{', '.join(str(t) for t in type_args)}>"


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
    from sushi_lang.semantics.typesys import BuiltinType, StructType, EnumType

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
