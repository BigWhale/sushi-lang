"""Generic type definitions for Sushi Lang."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar, Optional, Tuple, Union

from sushi_lang.semantics.generics.interned import interned_name

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, EnumVariantInfo
    from sushi_lang.semantics.ast import BoundedTypeParam
    from sushi_lang.internals.report import Span

TypeParam = Union['TypeParameter', 'BoundedTypeParam']

# What separates an opaque parameter from its template in an INTERNAL name
# (`List<T#main.f>`). It is never `<`, `>`, `,` or a space, because the type-argument
# splitter reads those, and a diagnostic never prints it (`display_type`; the spelling
# gate refuses it).
OPAQUE_MARK = "#"


@dataclass(frozen=True)
class TemplateId:
    """The identity of one template: the unit that declares it and its own name."""
    unit: Optional[str]
    name: str

    def __str__(self) -> str:
        return self.name if self.unit is None else f"{self.unit}.{self.name}"


@dataclass(frozen=True)
class TypeParameter:
    """A type parameter of a generic declaration.

    With no `owner`, it is a parameter as a template record stores it. With an `owner`,
    it is OPAQUE (#1070): the type parameter of that one template, in the body as the
    template check reads it. It stands for some type that satisfies `constraints`, and
    for nothing more. Identity is the name and the owner, so the `T` of `f` and the `T`
    of `g` are two types; the owner decides the constraints, so they take no part in it.

    An ELEMENT of a pack is an opaque parameter with an `element` number: the type that
    one `expand` of the pack binds in the check copy (#1070, R6). The number is part of
    the identity, because two `expand`s of one pack can bind two different types in a
    copy. `name` is the pack name.
    """
    name: str  # Parameter name (e.g., "T", "E", "U")
    owner: Optional[TemplateId] = None
    # The perk names (the table keys) of the constraints, as written.
    constraints: Tuple[str, ...] = field(default=(), compare=False)
    # Where the parameter is written, with its constraint list (`T: A + B`).
    span: Optional['Span'] = field(default=None, compare=False)
    # How a help spells a bound on it, with `{perk}` for the perk: `Box@(T: {perk})` for
    # a receiver parameter of an extension (#1070). None for the `@(T: {perk})` of a
    # declaration's own type-parameter list.
    bound_form: Optional[str] = field(default=None, compare=False)
    # The element number of an `expand` in the check copy, or None (#1070, R6).
    element: Optional[int] = None
    # The binder name and its span, for the note of a refusal. Not identity.
    binder: Optional[str] = field(default=None, compare=False)
    binder_span: Optional['Span'] = field(default=None, compare=False)
    # A bare parameter is never a pack; `BoundedTypeParam` carries the field that can be.
    is_pack: ClassVar[bool] = False

    @property
    def is_opaque(self) -> bool:
        return self.owner is not None

    def promises(self, perk: str) -> bool:
        """The ONE answer to "does a constraint of this opaque parameter promise `perk`"."""
        return self.is_opaque and perk in self.constraints

    def written(self) -> str:
        """The name as the source writes it: `...Ts` for an element of a pack."""
        return self.name if self.element is None else f"...{self.name}"

    def __str__(self) -> str:
        if self.owner is None:
            return self.name
        text = f"{self.name}{OPAQUE_MARK}{self.owner}"
        return text if self.element is None else f"{text}{OPAQUE_MARK}{self.element}"

    def __hash__(self) -> int:
        return hash(("type_param", self.name, self.owner, self.element))

    def __eq__(self, other) -> bool:
        return (isinstance(other, TypeParameter) and self.name == other.name
                and self.owner == other.owner and self.element == other.element)


@dataclass(frozen=True)
class TypePack:
    """A type-parameter bound to an ordered, variable-length pack of types."""
    types: Tuple[Type, ...]  # Ordered concrete types absorbed by the pack (zero or more)

    def __str__(self) -> str:
        inner = ", ".join(str(t) for t in self.types)
        return f"pack({inner})"


@dataclass(frozen=True)
class GenericEnumType:
    """A generic enum definition with type parameters."""
    name: str                                    # Generic enum name (e.g., "Result")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    variants: tuple[EnumVariantInfo, ...]        # Variants (may contain TypeParameters in associated types)
    is_error: bool = False                       # Every instance is an error type

    def __str__(self) -> str:
        return interned_name(self.name, self.type_params)

    def __hash__(self) -> int:
        return hash(("generic_enum", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericEnumType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.variants == other.variants)


@dataclass(frozen=True)
class GenericStructType:
    """A generic struct definition with type parameters."""
    name: str                                    # Generic struct name (e.g., "Pair", "Box")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    fields: tuple[tuple[str, Type], ...]         # Fields (may contain TypeParameters in field types)

    def __str__(self) -> str:
        return interned_name(self.name, self.type_params)

    def __hash__(self) -> int:
        return hash(("generic_struct", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericStructType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.fields == other.fields)


@dataclass(frozen=True)
class GenericTypeRef:
    """Reference to a generic type with concrete type arguments."""
    base_name: str                    # Generic type name (e.g., "Result", "Option", "Pair", "Box")
    type_args: tuple[Type, ...]       # Concrete type arguments (e.g., (BuiltinType.I32,))
    # The alias the base name was written behind. Identity ignores it, exactly as
    # `UnknownType.namespace` is ignored: the interned name is what `__str__` spells.
    namespace: Optional[str] = None

    def __str__(self) -> str:
        return interned_name(self.base_name, self.type_args)

    def __hash__(self) -> int:
        return hash(("generic_ref", self.base_name, self.type_args))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericTypeRef) and
                self.base_name == other.base_name and
                self.type_args == other.type_args)


def substitute_type_params(ty: Type, substitution: dict[str, Type]) -> Type:
    """Recursively put a type argument in the place of each type parameter it names.

    The PURE substitution: it rewrites a type and interns nothing, so a `Maybe@(T)` comes
    back as `Maybe@(i32)` rather than as the monomorphized `EnumType`. Its counterpart is
    `monomorphize.TypeSubstitutor.substitute_type`, which substitutes AND monomorphizes on
    demand, and is what the monomorphize pass uses.

    An unbound name passes through, rather than raising: a signature may legitimately name
    a type parameter this substitution does not bind.

    A NAMED type (StructType/EnumType) is terminal: its interned name already IS
    (declaration, type arguments), so there is nothing left to substitute (#240).

    Two arms carry #389, and each was missing from one of the two copies this replaces. A
    type parameter reaches a DECLARED position spelled as an **UnknownType** -- the collect
    pass converts only a TOP-LEVEL one to a `TypeParameter` -- and it is usually nested
    inside a **GenericTypeRef**, so `Maybe@(T)` in a generic-target extension's signature
    used to survive monomorphization untouched and report CE2001 on `T`.
    """
    from sushi_lang.semantics.typesys import (PointerType, ArrayType, DynamicArrayType,
                                              ReferenceType, FunctionType, UnknownType)

    if isinstance(ty, (TypeParameter, UnknownType)):
        return substitution.get(ty.name, ty)

    elif isinstance(ty, GenericTypeRef):
        return GenericTypeRef(
            base_name=ty.base_name,
            type_args=tuple(substitute_type_params(a, substitution) for a in ty.type_args),
        )

    elif isinstance(ty, PointerType):
        substituted_pointee = substitute_type_params(ty.pointee_type, substitution)
        return PointerType(pointee_type=substituted_pointee)

    elif isinstance(ty, ArrayType):
        substituted_base = substitute_type_params(ty.base_type, substitution)
        return ArrayType(base_type=substituted_base, size=ty.size)

    elif isinstance(ty, DynamicArrayType):
        substituted_base = substitute_type_params(ty.base_type, substitution)
        return DynamicArrayType(base_type=substituted_base)

    elif isinstance(ty, ReferenceType):
        substituted_ref = substitute_type_params(ty.referenced_type, substitution)
        return ReferenceType(referenced_type=substituted_ref, mutability=ty.mutability)

    elif isinstance(ty, FunctionType):
        # `replace`, so `param_modes` rides along beside `captures` (#368).
        return replace(
            ty,
            param_types=tuple(substitute_type_params(p, substitution) for p in ty.param_types),
            ok_type=substitute_type_params(ty.ok_type, substitution),
            err_type=(None if ty.err_type is None
                      else substitute_type_params(ty.err_type, substitution)),
        )

    else:
        return ty


def type_param_substitution(generic_func, type_args
                            ) -> Optional[dict[str, Union[Type, TypePack]]]:
    """Type parameter name -> type argument, read from the FLAT tuple a call names.

    The leading type parameters take the leading arguments one by one. A pack parameter
    is always the last one, and it takes every argument after them as one `TypePack`.
    ONE reader for the monomorphize pass, which builds the instance, and for the passes
    that type a call before the instance exists (#1160). None when the count does not
    fit the declaration.
    """
    params = list(generic_func.type_params)
    pack = params.pop() if params and params[-1].is_pack else None
    if any(tp.is_pack for tp in params):
        return None
    args = tuple(type_args)
    if len(args) < len(params) or (pack is None and len(args) != len(params)):
        return None
    substitution: dict[str, Union[Type, TypePack]] = {
        _param_name(tp): arg for tp, arg in zip(params, args, strict=False)}
    if pack is not None:
        substitution[_param_name(pack)] = TypePack(args[len(params):])
    return substitution


def _param_name(tp) -> str:
    return tp.name if hasattr(tp, "name") else str(tp)


def substituted_call_signature(generic_func, type_args):
    """The signature of a generic declaration with `type_args` put through it.

    `(params, ret, err)`: each parameter with its type substituted, the return, and the
    channel. The PURE substitution, so nothing is interned. None when the count does not
    fit the declaration, or the declaration has no return. One derivation for what a call
    yields (`substituted_call_result`) and for what a call in a template check passes
    (#1070), where no instance is cut.
    """
    from sushi_lang.semantics.generics.monomorphize.transformer import (
        fan_out_pack_param, pack_binding_for, substituted_param)
    substitution = type_param_substitution(generic_func, type_args)
    if substitution is None or generic_func.ret is None:
        return None
    params = []
    for p in generic_func.params:
        pack = pack_binding_for(p, substitution)
        if pack is not None:
            params += fan_out_pack_param(p, pack)
        else:
            params.append(substituted_param(p, substitute_type_params(p.ty, substitution)
                                            if p.ty is not None else None))
    ret = substitute_type_params(generic_func.ret, substitution)
    err = (substitute_type_params(generic_func.err_type, substitution)
           if generic_func.err_type is not None else None)
    return params, ret, err


def substituted_call_result(generic_func, type_args) -> Optional[Type]:
    """What a call to a generic declaration yields, read from its SUBSTITUTED signature.

    ONE derivation, because two passes ask the same question before the instance exists:
    the instantiate pass types a generic call that is a `match` scrutinee (#549), and the
    typecheck pass types one that is another generic call's argument (#556). Neither can
    go through the monomorphized copy -- it is not built yet -- so the template's return
    is substituted and wrapped exactly as the declaration wraps it: an explicit
    `Result@(T, E)` is its own two arms, anything else takes the spelled channel, and a
    BARE signature yields its substituted return (docs/design/error-channel.md).

    The PURE substitution is used, so nothing is interned here. A payload whose instance
    does not exist yet comes back as a `GenericTypeRef`, which is what keeps an early
    answer out of the enum table.
    """
    signature = substituted_call_signature(generic_func, type_args)
    if signature is None:
        return None
    _params, ret, err = signature
    if isinstance(ret, GenericTypeRef) and ret.base_name == "Result":
        return ret
    if err is None:
        return ret
    return GenericTypeRef(base_name="Result", type_args=(ret, err))


__all__ = ["OPAQUE_MARK", "TemplateId", "TypeParameter", "TypePack", "GenericEnumType", "GenericStructType",
           "GenericTypeRef", "substitute_type_params", "type_param_substitution",
           "substituted_call_result"]
