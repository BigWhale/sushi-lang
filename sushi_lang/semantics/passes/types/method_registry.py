"""The one table that says which built-in method family a call belongs to.

A method call is answered by a built-in family (an array method, a `Result` method, a
derived `clone`), by a perk implementation, or by an extension method. The families were
dispatched TWICE: the typecheck pass INFERRED through a registry of checkers and
VALIDATED through an `if/elif` chain of twelve arms in a different order, and nothing
held the two in step but two comments. The failure mode is a miscompile -- the pass types
the call as the built-in's return while the backend calls the perk implementation that
answers it (#751).

There is one table now. A `MethodFamily` carries the CLAIM -- which receiver and which
name it answers -- and both halves: the inferrer the typecheck pass reads, and the
validator it runs. `claim()` is the one decision; `infer_method_type()` and
`validate_method()` are two hooks on it. The gate is
`tests/unit/test_method_family_dispatch_is_one.py`, which holds the table TOTAL (no
family with one half) and its claims DISJOINT.

Disjoint is what makes the order safe: exactly one family answers any (receiver kind,
method name), so the order below decides nothing. It is written in the order the
validation half reads, because `docs/design/method-resolution.md` and the codegen
dispatcher state that one.

`beats_perk` is the one thing the order does decide. The ladder asks the perk
implementation BETWEEN the two halves of this table, so a family says which side it is
on. A family that yields to a perk asks the perk table in its own claim -- the primitive
and the two derived families do -- and a family that beats one has nothing to ask.
"""
from __future__ import annotations
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Protocol
from dataclasses import dataclass, field

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.cloning import DERIVED_CLONE_ARITY
from sushi_lang.semantics.generics.hashing import DERIVED_HASH_ARITY
from sushi_lang.semantics.generics.hashmap import HASHMAP_METHOD_ARITY
from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY
from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_ARITY
from sushi_lang.semantics.generics.own import OWN_METHOD_ARITY
from sushi_lang.semantics.generics.results import RESULT_METHOD_ARITY
from sushi_lang.semantics.generics.type_display import display_type

from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, FunctionType, StructType)

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.ast import MethodCall


class MethodTypeInferrer(Protocol):
    """Protocol for method type inference handlers."""

    def infer_return_type(self) -> Optional['Type']:
        """Infer the return type of a method call."""
        ...


#: Does this family answer this receiver and this method name?
ClaimHook = Callable[['Type', str, 'TypeValidator'], bool]
#: The inferrer the typecheck pass reads for a claimed call.
InferHook = Callable[['Type', str, 'TypeValidator'], MethodTypeInferrer]
#: The check the typecheck pass runs for a claimed call.
ValidateHook = Callable[['TypeValidator', 'MethodCall', 'Type'], None]


@dataclass
class MethodFamily:
    """One built-in method family: who it answers for, and what each half does.

    `arity` is the argument count of each method the family answers. The count is read
    before `validate` runs, and a miscount is CE2009 like every other callee (#799). A
    family whose module keeps its own count leaves the table empty.
    """
    name: str
    beats_perk: bool
    claims: ClaimHook
    infer: Optional[InferHook] = None
    validate: Optional[ValidateHook] = None
    arity: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


class MethodTypeRegistry:
    """The built-in method families, and the one claim both halves of the pass read."""

    def __init__(self):
        self._families: list[MethodFamily] = []
        self._by_name: dict[str, MethodFamily] = {}

    @property
    def families(self) -> tuple[MethodFamily, ...]:
        """The table, in dispatch order."""
        return tuple(self._families)

    def register(self, family: MethodFamily) -> MethodFamily:
        """Add a family. Its name is the handle the validation half attaches to."""
        if family.name in self._by_name:
            raise ValueError(f"method family {family.name!r} is registered twice")
        self._families.append(family)
        self._by_name[family.name] = family
        return family

    def family(self, name: str) -> MethodFamily:
        """The family registered under this name."""
        return self._by_name[name]

    def validator(self, name: str) -> Callable[[ValidateHook], ValidateHook]:
        """Attach the validation hook of a family the table already carries."""
        def attach(hook: ValidateHook) -> ValidateHook:
            family = self._by_name.get(name)
            if family is None:
                raise ValueError(f"no method family named {name!r}")
            family.validate = hook
            return hook
        return attach

    def claim(self, receiver_type: 'Type', method_name: str,
              validator: 'TypeValidator') -> Optional[MethodFamily]:
        """The family that answers this call, or None when the ladder gets it."""
        for family in self._families:
            if family.claims(receiver_type, method_name, validator):
                return family
        return None

    def infer_method_type(self, receiver_type: 'Type', method_name: str,
                          validator: 'TypeValidator') -> Optional['Type']:
        """Infer the return type of a method call."""
        family = self.claim(receiver_type, method_name, validator)
        if family is None or family.infer is None:
            return None
        return family.infer(receiver_type, method_name, validator).infer_return_type()

    def validate_method(self, validator: 'TypeValidator', call: 'MethodCall',
                        receiver_type: 'Type', *, beats_perk: bool) -> bool:
        """Run the claiming family's check, and answer whether one ran.

        `beats_perk` picks the half of the table the caller is asking for: the families
        that answer before a perk implementation does, or the ones that answer after it.
        """
        family = self.claim(receiver_type, call.method, validator)
        if family is None or family.beats_perk != beats_perk or family.validate is None:
            return False
        if reject_builtin_miscount(validator.reporter, call,
                                   f"{display_type(receiver_type)}.{call.method}",
                                   family.arity):
            return True
        family.validate(validator, call, receiver_type)
        return True


METHOD_TYPE_REGISTRY = MethodTypeRegistry()


def arity_of_family(name: str) -> Mapping[str, int]:
    """The argument count of each method the named family answers."""
    return METHOD_TYPE_REGISTRY.family(name).arity


@dataclass
class ArrayMethodInferrer:
    """Type inferrer for array methods."""
    receiver_type: ArrayType | DynamicArrayType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method, get_builtin_array_method_return_type
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        from sushi_lang.semantics.typesys import deref_type

        actual_type = deref_type(self.receiver_type)

        if is_builtin_array_method(self.method_name):
            # `.get()`, `.first()` and `.last()` READ and `.pop()` REMOVES, but all four
            # answer "there is no such element" the same way, so each is Maybe@(T) -- as
            # `List@(T)` already was. A bare `T` had to invent a value for the empty
            # case (#377).
            if self.method_name in ("get", "first", "last", "pop"):
                element_type = actual_type.base_type
                maybe_type = ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                return maybe_type

            # `index_of` answers WHERE, so its Maybe carries the index and not the
            # element -- the one array Maybe whose payload is not `base_type`.
            if self.method_name == "index_of":
                return ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32,
                                                  struct_table=self.validator.struct_table.by_name)

            return get_builtin_array_method_return_type(self.method_name, actual_type,
                                                        self.validator)
        return None


@dataclass
class StringMethodInferrer:
    """Type inferrer for string methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method, get_builtin_string_method_return_type
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if is_builtin_string_method(self.method_name):
            ret = get_builtin_string_method_return_type(self.method_name, BuiltinType.STRING)
            # The family table answers a Maybe as a SPELLING (it has no enum table);
            # interning it is this layer's job.
            if isinstance(ret, GenericTypeRef) and ret.base_name == "Maybe":
                return ensure_maybe_type_in_table(
                    self.validator.enum_table, ret.type_args[0],
                    struct_table=self.validator.struct_table.by_name)
            return ret
        return None


@dataclass
class PrimitiveMethodInferrer:
    """Type inferrer for built-in primitive methods (to_str, hash, to_bits)."""
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.primitives import primitive_method_return_type
        return primitive_method_return_type(self.receiver_type, self.method_name)


@dataclass
class StructEnumBuiltinInferrer:
    """Type inferrer for the auto-derived struct/enum builtins (hash, clone)."""
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        method = self.validator.derived_methods.get_method(
            self.receiver_type, self.method_name)
        if method is not None:
            return method.return_type
        return None


@dataclass
class ResultMethodInferrer:
    """Type inferrer for Result<T, E> methods."""
    receiver_type: EnumType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.results import is_builtin_result_method
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        if is_builtin_result_method(self.method_name):
            ok_variant = self.receiver_type.get_variant("Ok")
            err_variant = self.receiver_type.get_variant("Err")

            if self.method_name in ("is_ok", "is_err"):
                return BuiltinType.BOOL
            elif self.method_name == "realise":
                if ok_variant and ok_variant.associated_types:
                    return ok_variant.associated_types[0]
            elif self.method_name == "expect":
                if ok_variant and ok_variant.associated_types:
                    return ok_variant.associated_types[0]
            elif self.method_name == "err":
                if err_variant and err_variant.associated_types:
                    err_type = err_variant.associated_types[0]
                    return ensure_maybe_type_in_table(self.validator.enum_table, err_type, struct_table=self.validator.struct_table.by_name)
        return None


@dataclass
class MaybeMethodInferrer:
    """Type inferrer for Maybe<T> methods."""
    receiver_type: EnumType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method, maybe_method_return_type
        if is_builtin_maybe_method(self.method_name):
            some_variant = self.receiver_type.get_variant("Some")
            if some_variant and some_variant.associated_types:
                return maybe_method_return_type(
                    some_variant.associated_types[0], self.method_name)
        return None


@dataclass
class HashMapMethodInferrer:
    """Type inferrer for HashMap<K, V> methods."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method, parse_hashmap_types
        if is_builtin_hashmap_method(self.method_name):
            key_type, value_type = parse_hashmap_types(self.receiver_type, self.validator)
            if key_type is not None and value_type is not None:
                if self.method_name in ("get", "remove"):
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, value_type, struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "clone":
                    # `.clone()` is the ONLY escape from CE2411 for a HashMap read, so it must
                    # exist for every HashMap. Returns the receiver's own type.
                    return self.receiver_type
                elif self.method_name in ("contains_key", "is_empty"):
                    return BuiltinType.BOOL
                elif self.method_name in ("len", "tombstone_count"):
                    return BuiltinType.I32
                elif self.method_name in ("new", "insert", "rehash", "debug", "free", "destroy"):
                    return BuiltinType.BLANK
                elif self.method_name == "keys":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=key_type)
                elif self.method_name == "values":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=value_type)
                elif self.method_name == "entries":
                    from sushi_lang.semantics.typesys import IteratorType
                    from sushi_lang.semantics.generics.hashmap import ensure_entry_type_in_struct_table
                    entry_type = ensure_entry_type_in_struct_table(
                        self.validator.struct_table, self.validator.derived_methods,
                        key_type, value_type
                    )
                    return IteratorType(element_type=entry_type)
        return None


@dataclass
class ListMethodInferrer:
    """Type inferrer for List<T> methods."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.list import is_builtin_list_method
        from sushi_lang.semantics.generics.list import parse_list_types

        if is_builtin_list_method(self.method_name):
            element_type = parse_list_types(self.receiver_type, self.validator)
            if element_type is not None:
                if self.method_name in ("get", "pop", "remove"):
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "clone":
                    # `.clone()` is the ONLY escape from CE2411 for a List read, so it must
                    # exist for every List (#242). Returns the receiver's own type.
                    return self.receiver_type
                elif self.method_name in ("len", "capacity"):
                    return BuiltinType.I32
                elif self.method_name == "is_empty":
                    return BuiltinType.BOOL
                elif self.method_name == "insert":
                    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                    std_error = self.validator.enum_table.by_name.get("StdError")
                    if std_error is None:
                        return None
                    return ensure_result_type_in_table(self.validator.enum_table, BuiltinType.BLANK, std_error,
                                       struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "iter":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=element_type)
                elif self.method_name in ("new", "with_capacity", "push", "clear",
                                         "reserve", "shrink_to_fit", "destroy", "free", "debug"):
                    return BuiltinType.BLANK
        return None


@dataclass
class OwnMethodInferrer:
    """Type inferrer for Own<T> methods called on an Own value (.get(), .destroy())."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.own import get_own_element_type
        if self.method_name == "get":
            try:
                return get_own_element_type(self.receiver_type)
            except (TypeError, IndexError):
                return None
        if self.method_name == "destroy":
            return BuiltinType.BLANK
        if self.method_name == "clone":
            # A fresh Own@(T) over a copied payload -- the receiver's own type (#242).
            return self.receiver_type
        return None


@dataclass
class FunctionMethodInferrer:
    """Type inferrer for the built-in methods on a function value (.clone())."""
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.closures import function_method_return_type
        return function_method_return_type(self.method_name, self.receiver_type)


# The family table. Written in the order the VALIDATION half reads: the families that
# answer before a perk implementation, then the ones that answer after it. The claims are
# disjoint, so the order changes no answer; stating ONE order in both layers is the point
# (#273), and `tests/unit/test_method_resolution_family_order.py` pins it against the
# codegen dispatcher.
#
# A claim reads the receiver type as the caller hands it in. Both callers deref a borrow
# before they ask -- the inference half in `visitor.py:visit_methodcall`, the validation
# half because `infer_expression_type` of a receiver never answered a `ReferenceType` in
# a sweep of 984 fixtures over the five areas that bind one -- so no claim derefs.


def _named(receiver_type: 'Type', kind: type, base: str) -> bool:
    """A receiver of this kind that is an instance of this generic base."""
    return isinstance(receiver_type, kind) and is_instance_of(receiver_type, base)


def _has_perk_override(receiver_type: 'Type', method_name: str,
                       validator: 'TypeValidator') -> bool:
    """Whether a perk implementation answers this call instead.

    A perk implementation is the sanctioned override and wins at validation and at
    codegen, so a family that the ladder asks AFTER the perk has to decline here or the
    two halves disagree about which method the call names.
    """
    return validator.perk_impl_table.get_method(receiver_type, method_name) is not None


def _claims_array(receiver_type, method_name, validator):
    from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method
    return (isinstance(receiver_type, (ArrayType, DynamicArrayType))
            and is_builtin_array_method(method_name))


def _claims_string(receiver_type, method_name, validator):
    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    return receiver_type == BuiltinType.STRING and is_builtin_string_method(method_name)


def _claims_result(receiver_type, method_name, validator):
    from sushi_lang.semantics.generics.results import is_builtin_result_method
    return (_named(receiver_type, EnumType, "Result")
            and is_builtin_result_method(method_name))


def _claims_maybe(receiver_type, method_name, validator):
    from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
    return (_named(receiver_type, EnumType, "Maybe")
            and is_builtin_maybe_method(method_name))


def _claims_own(receiver_type, method_name, validator):
    from sushi_lang.semantics.generics.own import is_builtin_own_method
    return (_named(receiver_type, StructType, "Own")
            and is_builtin_own_method(method_name))


def _claims_hashmap(receiver_type, method_name, validator):
    from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method
    return (_named(receiver_type, StructType, "HashMap")
            and is_builtin_hashmap_method(method_name))


def _claims_list(receiver_type, method_name, validator):
    from sushi_lang.semantics.generics.list import is_builtin_list_method
    return (_named(receiver_type, StructType, "List")
            and is_builtin_list_method(method_name))


def _claims_derived_hash(receiver_type, method_name, validator):
    # A container hashes what it HOLDS, so `List@(T).hash()` is a derived method and not
    # the container family's (#628). Declining it here left the container inferrer to
    # answer None, and a call with NO inferred type is not compared against its declared
    # one -- `let i32 h = l.hash()` was accepted in silence.
    return (isinstance(receiver_type, (StructType, EnumType))
            and method_name == "hash"
            and not _has_perk_override(receiver_type, method_name, validator)
            and validator.derived_methods.get_method(receiver_type, "hash") is not None)


def _claims_derived_clone(receiver_type, method_name, validator):
    # `Own`, `List` and `HashMap` keep their own clone, and the derive pass registers
    # none for them (`generics/cloning.py`), so the base test states what the table
    # already holds.
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    return (isinstance(receiver_type, (StructType, EnumType))
            and method_name == "clone"
            and not is_instance_of(receiver_type, *CONTAINER_BASES)
            and not _has_perk_override(receiver_type, method_name, validator)
            and validator.derived_methods.get_method(receiver_type, "clone") is not None)


def _claims_function(receiver_type, method_name, validator):
    # No perk question: a function type is not an extension target
    # (`generics/extension_targets.py:CONCRETE_EXTENSION_TARGETS`), so no perk
    # implementation can name one.
    from sushi_lang.semantics.generics.closures import is_builtin_function_method
    return (isinstance(receiver_type, FunctionType)
            and is_builtin_function_method(method_name))


def _claims_primitive(receiver_type, method_name, validator):
    # Every primitive INCLUDING string. `has_primitive_method` answers for the (receiver,
    # name) pair and not for the name alone: `to_bits` exists on f32 and f64 and nowhere
    # else, so `i32.to_bits()` falls through to a clean unknown-method error.
    from sushi_lang.semantics.generics.primitives import has_primitive_method
    return (has_primitive_method(receiver_type, method_name)
            and not _has_perk_override(receiver_type, method_name, validator))


METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="array", beats_perk=True, claims=_claims_array,
    infer=lambda rt, name, v: ArrayMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="string", beats_perk=True, claims=_claims_string,
    infer=lambda rt, name, v: StringMethodInferrer(name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="result", beats_perk=True, claims=_claims_result, arity=RESULT_METHOD_ARITY,
    infer=lambda rt, name, v: ResultMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="maybe", beats_perk=True, claims=_claims_maybe, arity=MAYBE_METHOD_ARITY,
    infer=lambda rt, name, v: MaybeMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="own", beats_perk=True, claims=_claims_own, arity=OWN_METHOD_ARITY,
    infer=lambda rt, name, v: OwnMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="hashmap", beats_perk=True, claims=_claims_hashmap, arity=HASHMAP_METHOD_ARITY,
    infer=lambda rt, name, v: HashMapMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="list", beats_perk=True, claims=_claims_list, arity=LIST_METHOD_ARITY,
    infer=lambda rt, name, v: ListMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="derived_hash", beats_perk=False, claims=_claims_derived_hash, arity=DERIVED_HASH_ARITY,
    infer=lambda rt, name, v: StructEnumBuiltinInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="derived_clone", beats_perk=False, claims=_claims_derived_clone, arity=DERIVED_CLONE_ARITY,
    infer=lambda rt, name, v: StructEnumBuiltinInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="function", beats_perk=False, claims=_claims_function,
    infer=lambda rt, name, v: FunctionMethodInferrer(rt, name, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="primitive", beats_perk=False, claims=_claims_primitive,
    infer=lambda rt, name, v: PrimitiveMethodInferrer(rt, name, v)))
