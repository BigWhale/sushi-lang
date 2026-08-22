"""What the `@(...)` arguments of an extension target MEAN (#393).

`extend Box@(T)` and `extend Box@(Point)` are spelled the same way -- both arguments parse as
an `UnknownType` -- and they mean opposite things. The first names a type PARAMETER and
applies to every instantiation of `Box`; the second is a CONSTRAINT and applies to
`Box<Point>` and to nothing else, exactly as a perk implementation on the same target
already did.

The question is answered ONCE, in the collect pass, where the struct and enum tables say which names
are declared types. The answer is carried on the declaration (`ExtendDef.target_shape`) and
on its collected signature (`GenericExtensionMethod`), so the instantiate and monomorphize passes read it
rather than deciding again -- two decisions from two sets of visible types could disagree,
and the disagreement would be silent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from sushi_lang.semantics.typesys import Type, UnknownType
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter


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
