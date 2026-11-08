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
validator it runs. `claim()` is the one decision, and `validate_method()` reads it. The gate is
`tests/unit/test_method_family_dispatch_is_one.py`, which holds the table TOTAL (no
family with one half) and its claims DISJOINT.

Disjoint is what makes the order safe: exactly one family answers any (receiver kind,
method name), so the order below decides nothing. It is written in the order the
validation half reads, because `docs/design/method-resolution.md` and the codegen
dispatcher state that one.

`beats_perk` is the one thing the order does decide. The ladder asks the perk
implementation BETWEEN the two halves of this table, so a family says which side it is
on. A family that yields to a perk declines a call that a perk implementation answers,
and a family that beats one has nothing to ask.

A family's ANSWER is the other half of its claim: whether the compiler defines this name
on this receiver, with no perk question. `builtin_method_exists` (CE2097) reads the
answers and nothing else, so the table is the one home of "is this a compiler-defined
method" (#812). The gate is `tests/unit/test_builtin_method_seam.py`.
"""
from __future__ import annotations
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Protocol
from dataclasses import dataclass, field

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.cloning import DERIVED_CLONE_ARITY
from sushi_lang.semantics.generics.contracts import CONTRACT_METHOD_ARITY
from sushi_lang.semantics.generics.hashing import DERIVED_HASH_ARITY
from sushi_lang.semantics.generics.hashmap import HASHMAP_METHOD_ARITY, HASHMAP_METHOD_MODES
from sushi_lang.semantics.generics.iterators import ITERATOR_METHOD_ARITY, iterator_method_modes
from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY, LIST_METHOD_MODES
from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_ARITY, MAYBE_METHOD_MODES
from sushi_lang.semantics.generics.primitives import PRIMITIVE_METHOD_ARITY
from sushi_lang.semantics.generics.own import OWN_METHOD_ARITY, OWN_METHOD_MODES
from sushi_lang.semantics.generics.results import RESULT_METHOD_ARITY, RESULT_METHOD_MODES
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import (
    ARRAY_FAMILY, ARRAY_HASH_FAMILY, CONTRACT_FAMILY, DERIVED_HASH_FAMILY, MAYBE_FAMILY,
    PRIMITIVE_FAMILY, RESULT_FAMILY, STRING_FAMILY)
from sushi_lang.semantics.param_modes import BuiltinModes
from sushi_lang.semantics.passes.types.arrays import ARRAY_METHOD_MODES
from sushi_lang.semantics.foreign_memory import (
    FOREIGN_PTR_METHODS, FOREIGN_PTR_METHOD_ARITY, foreign_ptr_return_type)
from sushi_lang.semantics.shir.callee import BuiltinMethod

from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, ForeignPtrType, FunctionType,
    IteratorType, StructType)

if TYPE_CHECKING:
    from sushi_lang.semantics.derived_methods import DerivedMethodTable
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.ast import MethodCall


class MethodTypeInferrer(Protocol):
    """Protocol for method type inference handlers."""

    def infer_return_type(self) -> Optional['Type']:
        """Infer the return type of a method call."""
        ...


#: Does the compiler define this method name on this receiver? No perk question.
AnswerHook = Callable[['Type', str, 'DerivedMethodTable'], bool]
#: The inferrer the typecheck pass reads for a claimed call. It receives the CALL, so a
#: return type can depend on an argument (`or_err`, docs/design/error-conversion.md 8.3).
InferHook = Callable[['Type', 'MethodCall', 'TypeValidator'], MethodTypeInferrer]
#: The check the typecheck pass runs for a claimed call.
ValidateHook = Callable[['TypeValidator', 'MethodCall', 'Type'], None]


def _has_perk_override(receiver_type: 'Type', method_name: str,
                       validator: 'TypeValidator') -> bool:
    """Whether a perk implementation answers this call instead.

    A perk implementation is the sanctioned override and wins at validation and at
    codegen, so a family that the ladder asks AFTER the perk has to decline or the two
    halves disagree about which method the call names.
    """
    return validator.perk_impl_table.get_method(receiver_type, method_name) is not None


@dataclass
class MethodFamily:
    """One built-in method family: who it answers for, and what each half does.

    `arity` is the argument count of each method the family answers. The count is read
    before `validate` runs, and a miscount is CE2009 like every other callee (#799). A
    family whose module keeps its own count leaves the table empty.

    `modes` is the parameter mode of each argument (#1173). The typecheck pass stamps it
    on the call, and the `borrow` pass checks each marker by the rule of a user method.
    """
    name: str
    beats_perk: bool
    answers: AnswerHook
    infer: Optional[InferHook] = None
    validate: Optional[ValidateHook] = None
    arity: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    modes: BuiltinModes = field(default_factory=BuiltinModes)

    def callee(self, method_name: str) -> BuiltinMethod:
        """The method of this family as a callee of SHIR (docs/design/ir.md 7.6). The one
        maker of a `BuiltinMethod`, so its family is always a row of the table, and the
        receiver mode of the method is the one of the table of the family."""
        return BuiltinMethod(self.name, method_name,
                             nom_self=method_name in self.modes.nom_self)

    def claims(self, receiver_type: 'Type', method_name: str,
               validator: 'TypeValidator') -> bool:
        """Does this family answer this call? A family that yields to a perk declines
        the call that a perk implementation answers."""
        if not self.answers(receiver_type, method_name, validator.derived_methods):
            return False
        return self.beats_perk or not _has_perk_override(receiver_type, method_name,
                                                         validator)


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

    def answers(self, receiver_type: 'Type', method_name: str,
                derived_methods: 'DerivedMethodTable') -> bool:
        """Does any family define this method name on this receiver? No perk question:
        a perk implementation overrides a built-in, it does not remove it."""
        return any(family.answers(receiver_type, method_name, derived_methods)
                   for family in self._families)

    def validate_method(self, validator: 'TypeValidator', call: 'MethodCall',
                        receiver_type: 'Type', *, beats_perk: bool) -> bool:
        """Run the claiming family's check, and answer whether one ran.

        `beats_perk` picks the half of the table the caller is asking for: the families
        that answer before a perk implementation does, or the ones that answer after it.
        """
        family = self.claim(receiver_type, call.method, validator)
        if family is None or family.beats_perk != beats_perk or family.validate is None:
            return False
        call.callee_builtin_family = family.name
        if reject_builtin_miscount(validator.reporter, call.loc, call.method, len(call.args),
                                   f"{display_type(receiver_type)}.{call.method}",
                                   family.arity):
            return True
        family.validate(validator, call, receiver_type)
        stamp_builtin_modes(call, family.modes)
        return True


def stamp_builtin_modes(call: 'MethodCall', modes: BuiltinModes) -> None:
    """Stamp the parameter modes of a built-in call for the `borrow` pass (#1173).

    A method with a signature row (`or_err`, `map_err`) stamped its own in `validate`. A
    call into a container slot gets no stamp: the slot takes ownership by position.
    """
    if call.callee_param_modes is None:
        call.callee_param_modes = modes.of(call.method, len(call.args))


METHOD_TYPE_REGISTRY = MethodTypeRegistry()


class LazyStringArity(Mapping[str, int]):
    """The arity row of the string family, read from the generator package on first use,
    so that importing the registry does not import the generators."""

    def _row(self) -> Mapping[str, int]:
        from sushi_lang.sushi_stdlib.src.collections.strings import STRING_METHOD_ARITY
        return STRING_METHOD_ARITY

    def __getitem__(self, name: str) -> int:
        return self._row()[name]

    def __iter__(self):
        return iter(self._row())

    def __len__(self) -> int:
        return len(self._row())


def producer_iterator(validator: 'TypeValidator', item: Optional['Type']) -> Optional['Type']:
    """What a container's iterator method answers: `Iterator@(peek T)` (#1259)."""
    from sushi_lang.semantics.passes.types.utils import producer_iterator as answer
    return answer(validator, item)


def arity_of_family(name: str) -> Mapping[str, int]:
    """The argument count of each method the named family answers."""
    return METHOD_TYPE_REGISTRY.family(name).arity


@dataclass
class ArrayMethodInferrer:
    """Type inferrer for array methods: the answer of the row of the method."""
    receiver_type: ArrayType | DynamicArrayType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.passes.types.arrays import array_method_return_type
        return array_method_return_type(self.validator, self.receiver_type,
                                        self.method_name)


def string_method_return_type(validator: 'TypeValidator', method_name: str) -> Optional['Type']:
    """The type that a built-in string method yields, from its row.

    The row spells a `Maybe` and a tuple as a written type (it has no enum table), so
    this layer interns it.
    """
    from sushi_lang.sushi_stdlib.src.collections.strings import (
        is_builtin_string_method, get_builtin_string_method_return_type)
    from sushi_lang.semantics.passes.types.utils import intern_declared_wrapper
    if not is_builtin_string_method(method_name):
        return None
    ret = get_builtin_string_method_return_type(method_name, BuiltinType.STRING)
    interned = intern_declared_wrapper(validator, ret)
    return interned if interned is not None else ret


@dataclass
class StringMethodInferrer:
    """Type inferrer for string methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        return string_method_return_type(self.validator, self.method_name)


@dataclass
class PrimitiveMethodInferrer:
    """Type inferrer for built-in primitive methods (to_str, hash, to_bits, the bit methods)."""
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
class ContractMethodInferrer:
    """Type inferrer for `eq`, `compare` and `to_str`, the derived contract methods."""
    method_name: str

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.contracts import CONTRACT_METHOD_RETURN
        return CONTRACT_METHOD_RETURN.get(self.method_name)


@dataclass
class ResultMethodInferrer:
    """Type inferrer for Result<T, E> methods."""
    receiver_type: EnumType
    call: 'MethodCall'
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.results import (
            RESULT_METHOD_SIGNATURES, result_method_row)
        signature = RESULT_METHOD_SIGNATURES.get(self.call.method)
        if signature is not None:
            from sushi_lang.semantics.passes.types.calls.builtin_signature import (
                builtin_call_return_type)
            return builtin_call_return_type(self.validator, signature,
                                            self.receiver_type, self.call)
        row = result_method_row(self.receiver_type, self.call.method,
                                self.validator.enum_table, self.validator.struct_table.by_name)
        return None if row is None else row.returns


@dataclass
class MaybeMethodInferrer:
    """Type inferrer for Maybe<T> methods."""
    receiver_type: EnumType
    call: 'MethodCall'
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.maybe import (
            MAYBE_METHOD_SIGNATURES, maybe_method_return_type, maybe_payload)
        signature = MAYBE_METHOD_SIGNATURES.get(self.call.method)
        if signature is not None:
            from sushi_lang.semantics.passes.types.calls.builtin_signature import (
                builtin_call_return_type)
            return builtin_call_return_type(self.validator, signature,
                                            self.receiver_type, self.call)
        return maybe_method_return_type(maybe_payload(self.receiver_type), self.call.method)


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
                    return producer_iterator(self.validator, key_type)
                elif self.method_name == "values":
                    return producer_iterator(self.validator, value_type)
                elif self.method_name == "entries":
                    from sushi_lang.semantics.generics.hashmap import ensure_entry_type_in_struct_table
                    entry_type = ensure_entry_type_in_struct_table(
                        self.validator.struct_table, self.validator.derived_methods,
                        key_type, value_type
                    )
                    return producer_iterator(self.validator, entry_type)
                elif self.method_name == "pairs":
                    from sushi_lang.semantics.generics.tuples import intern_tuple
                    pair_type = intern_tuple(self.validator.struct_table,
                                             self.validator.enum_table, (key_type, value_type))
                    return producer_iterator(self.validator, pair_type)
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
                if self.method_name in ("get", "first", "last", "pop", "remove"):
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                elif self.method_name in ("index_of", "index_of_from"):
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32,
                                                      struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "clone":
                    # `.clone()` is the ONLY escape from CE2411 for a List read, so it must
                    # exist for every List (#242). Returns the receiver's own type.
                    return self.receiver_type
                elif self.method_name in ("len", "capacity"):
                    return BuiltinType.I32
                elif self.method_name in ("is_empty", "contains"):
                    return BuiltinType.BOOL
                elif self.method_name == "insert":
                    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                    std_error = self.validator.enum_table.by_name.get("StdError")
                    if std_error is None:
                        return None
                    return ensure_result_type_in_table(self.validator.enum_table, BuiltinType.BLANK, std_error,
                                       struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "iter":
                    return producer_iterator(self.validator, element_type)
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
        if self.method_name in ("clone", "alloc"):
            # A fresh Own@(T): a copied payload (#242), or the argument of `alloc`.
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


@dataclass
class ForeignPtrMethodInferrer:
    """Type inferrer for the foreign-memory methods on a `ptr` (#1086)."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        return foreign_ptr_return_type(self.method_name, self.validator.enum_table,
                                       self.validator.struct_table.by_name)


# The family table. Written in the order the VALIDATION half reads: the families that
# answer before a perk implementation, then the ones that answer after it. The claims are
# disjoint, so the order changes no answer; stating ONE order is the point (#273), and
# `tests/unit/test_method_resolution_family_order.py` pins it.
#
# A claim reads the receiver type as the caller hands it in. Both callers deref a borrow
# before they ask -- the inference half in `visitor.py:visit_methodcall`, the validation
# half because `infer_expression_type` of a receiver never answered a `ReferenceType` in
# a sweep of 984 fixtures over the five areas that bind one -- so no claim derefs.


def _named(receiver_type: 'Type', kind: type, base: str) -> bool:
    """A receiver of this kind that is an instance of this generic base."""
    return isinstance(receiver_type, kind) and is_instance_of(receiver_type, base)


def _answers_array(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method
    return (isinstance(receiver_type, (ArrayType, DynamicArrayType))
            and method_name != ARRAY_HASH
            and is_builtin_array_method(method_name))


# An array's `hash` is the one array method a perk overrides: a `Hashable`
# implementation wins in every position, a direct call included (#699).
ARRAY_HASH = "hash"


def _answers_array_hash(receiver_type, method_name, _derived_methods):
    return (isinstance(receiver_type, (ArrayType, DynamicArrayType))
            and method_name == ARRAY_HASH)


def _answers_string(receiver_type, method_name, derived_methods):
    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    return receiver_type == BuiltinType.STRING and is_builtin_string_method(method_name)


def _answers_result(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.generics.results import is_builtin_result_method
    return (_named(receiver_type, EnumType, "Result")
            and is_builtin_result_method(method_name))


def _answers_maybe(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
    return (_named(receiver_type, EnumType, "Maybe")
            and is_builtin_maybe_method(method_name))


def _answers_own(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.generics.own import is_builtin_own_method
    return (_named(receiver_type, StructType, "Own")
            and is_builtin_own_method(method_name))


def _answers_hashmap(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method
    return (_named(receiver_type, StructType, "HashMap")
            and is_builtin_hashmap_method(method_name))


def _answers_list(receiver_type, method_name, derived_methods):
    from sushi_lang.semantics.generics.list import is_builtin_list_method
    return (_named(receiver_type, StructType, "List")
            and is_builtin_list_method(method_name))


def _answers_derived_hash(receiver_type, method_name, derived_methods):
    # A container hashes what it HOLDS, so `List@(T).hash()` is a derived method and not
    # the container family's (#628). Declining it here left the container inferrer to
    # answer None, and a call with NO inferred type is not compared against its declared
    # one -- `let i32 h = l.hash()` was accepted in silence.
    return (isinstance(receiver_type, (StructType, EnumType))
            and method_name == "hash"
            and derived_methods.get_method(receiver_type, "hash") is not None)


def _answers_derived_clone(receiver_type, method_name, derived_methods):
    # `Own`, `List` and `HashMap` keep their own clone, and the derive pass registers
    # none for them (`generics/cloning.py`), so the base test states what the table
    # already holds.
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    return (isinstance(receiver_type, (StructType, EnumType))
            and method_name == "clone"
            and not is_instance_of(receiver_type, *CONTAINER_BASES)
            and derived_methods.get_method(receiver_type, "clone") is not None)


def _answers_contract(receiver_type, method_name, derived_methods):
    # `eq`, `compare` and `to_str`: the methods of the three derived contracts. A
    # primitive has `eq` and `compare` (a bool included, which orders false before
    # true), and keeps its own `to_str` in the primitive family. A struct or an enum
    # answers when the contract can read it; an implementation of the contract takes
    # the call first, because this family yields to a perk.
    from sushi_lang.semantics.generics.contracts import (
        DISPLAY, METHOD_CONTRACT, operand_contract, override_of)
    contract = METHOD_CONTRACT.get(method_name)
    if contract is None:
        return False
    if isinstance(receiver_type, BuiltinType):
        return contract != DISPLAY and receiver_type != BuiltinType.BLANK
    if derived_methods is None:
        return False
    return operand_contract(receiver_type, contract,
                            overridden=override_of(derived_methods, contract))[0]


def _answers_function(receiver_type, method_name, derived_methods):
    # The perk question finds nothing: a function type is not an extension target
    # (`generics/extension_targets.py:CONCRETE_EXTENSION_TARGETS`), so no perk
    # implementation can name one.
    from sushi_lang.semantics.generics.closures import is_builtin_function_method
    return (isinstance(receiver_type, FunctionType)
            and is_builtin_function_method(method_name))


def _answers_iterator(receiver_type, method_name, derived_methods):
    # `clone()` copies the cursor (#1259), and the adapters (#1228). No perk can name an
    # `Iterator@(T)`: it is no extension target.
    from sushi_lang.semantics.generics.iterators import ADAPTERS
    return isinstance(receiver_type, IteratorType) and (
        method_name == "clone" or method_name in ADAPTERS)


@dataclass
class IteratorMethodInferrer:
    """Type inferrer for the built-in methods on an iterator value: `.clone()` and the
    adapters, whose result the arguments solve (`iterator_adapters.adapter_result`)."""
    receiver_type: 'Type'
    call: 'MethodCall'
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.iterators import ITERATOR_METHOD_SIGNATURES
        if self.call.method == "clone":
            return self.receiver_type
        signature = ITERATOR_METHOD_SIGNATURES.get(self.call.method)
        if signature is None:
            return None
        from sushi_lang.semantics.passes.types.calls.builtin_signature import (
            solve_builtin_call)
        from sushi_lang.semantics.passes.types.iterator_adapters import (
            adapter_result, settle_adapter_instance)
        from sushi_lang.semantics.typesys import deref_type
        receiver = deref_type(self.receiver_type)
        instance, _unsolved = solve_builtin_call(self.validator, signature, receiver,
                                                 self.call)
        if instance is None:
            return None
        instance = settle_adapter_instance(self.validator, self.call, instance)
        return adapter_result(self.validator, self.call, self.call.method, receiver, instance,
                              report=False)


def _answers_foreign_ptr(receiver_type, method_name, derived_methods):
    # No perk can name a `ptr`, so which side of the perk the family stands on decides
    # nothing; it is written with the other families that answer first.
    return isinstance(receiver_type, ForeignPtrType) and method_name in FOREIGN_PTR_METHODS


def _answers_primitive(receiver_type, method_name, derived_methods):
    # Every primitive INCLUDING string. `has_primitive_method` answers for the (receiver,
    # name) pair and not for the name alone: `to_bits` exists on f32 and f64 and nowhere
    # else, so `i32.to_bits()` falls through to a clean unknown-method error.
    from sushi_lang.semantics.generics.primitives import has_primitive_method
    return has_primitive_method(receiver_type, method_name)


METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=ARRAY_FAMILY, beats_perk=True, answers=_answers_array, modes=ARRAY_METHOD_MODES,
    infer=lambda rt, call, v: ArrayMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=STRING_FAMILY, beats_perk=True, answers=_answers_string, arity=LazyStringArity(),
    infer=lambda _receiver, call, v: StringMethodInferrer(call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=RESULT_FAMILY, beats_perk=True, answers=_answers_result, arity=RESULT_METHOD_ARITY,
    modes=RESULT_METHOD_MODES,
    infer=lambda rt, call, v: ResultMethodInferrer(rt, call, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=MAYBE_FAMILY, beats_perk=True, answers=_answers_maybe, arity=MAYBE_METHOD_ARITY,
    modes=MAYBE_METHOD_MODES,
    infer=lambda rt, call, v: MaybeMethodInferrer(rt, call, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="own", beats_perk=True, answers=_answers_own, arity=OWN_METHOD_ARITY,
    modes=OWN_METHOD_MODES,
    infer=lambda rt, call, v: OwnMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="hashmap", beats_perk=True, answers=_answers_hashmap, arity=HASHMAP_METHOD_ARITY,
    modes=HASHMAP_METHOD_MODES,
    infer=lambda rt, call, v: HashMapMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="list", beats_perk=True, answers=_answers_list, arity=LIST_METHOD_ARITY,
    modes=LIST_METHOD_MODES,
    infer=lambda rt, call, v: ListMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="iterator", beats_perk=True, answers=_answers_iterator,
    arity=ITERATOR_METHOD_ARITY, modes=iterator_method_modes(),
    infer=lambda rt, call, v: IteratorMethodInferrer(rt, call, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="foreign_ptr", beats_perk=True, answers=_answers_foreign_ptr,
    arity=FOREIGN_PTR_METHOD_ARITY,
    infer=lambda _receiver, call, v: ForeignPtrMethodInferrer(call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=ARRAY_HASH_FAMILY, beats_perk=False, answers=_answers_array_hash, arity=DERIVED_HASH_ARITY,
    infer=lambda rt, call, v: ArrayMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=DERIVED_HASH_FAMILY, beats_perk=False, answers=_answers_derived_hash, arity=DERIVED_HASH_ARITY,
    infer=lambda rt, call, v: StructEnumBuiltinInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="derived_clone", beats_perk=False, answers=_answers_derived_clone, arity=DERIVED_CLONE_ARITY,
    infer=lambda rt, call, v: StructEnumBuiltinInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=CONTRACT_FAMILY, beats_perk=False, answers=_answers_contract,
    arity=CONTRACT_METHOD_ARITY,
    infer=lambda _receiver, call, _validator: ContractMethodInferrer(call.method)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name="function", beats_perk=False, answers=_answers_function, arity={"clone": 0},
    infer=lambda rt, call, v: FunctionMethodInferrer(rt, call.method, v)))
METHOD_TYPE_REGISTRY.register(MethodFamily(
    name=PRIMITIVE_FAMILY, beats_perk=False, answers=_answers_primitive,
    arity=PRIMITIVE_METHOD_ARITY,
    infer=lambda rt, call, v: PrimitiveMethodInferrer(rt, call.method, v)))
