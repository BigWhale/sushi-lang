"""Built-in extension methods for array types (fixed and dynamic arrays).

ONE table, `_ARRAY_METHODS`, holds the whole set. A row gives a method's arity, the
receiver kinds it accepts, the rule its arguments must meet and the type it answers, so the
membership predicate, the validator and the return-type reader are three readings of one
table. Before #750 they were three hand-kept name lists and twelve near-identical
validators, and the lists had already drifted apart: `to_string_checked` stood in two of
the three, and its answer was patched in at `method_registry.py`.
`tests/unit/test_array_method_table_is_total.py` is the gate, and it reads the BACKEND
dispatcher too, because its emitters are the fourth list of the same names.

Two spellings are kept as they stood, because #750 promised no change of behaviour: the
bulk-copy family reports an arity fault with the internal CE0023 where every other row
reports CE2009, and `extend`/`extend_range` answer a type for a fixed receiver although the
validator refuses one there.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional, Sequence, Union

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ErrorMessage, Span
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Expr, MethodCall
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.typesys import (ArrayType, BuiltinType, DynamicArrayType,
                                          IteratorType, Type, deref_type)
from .utils import validate_constant_array_index
from sushi_lang.semantics.type_predicates import is_integer_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


ArrayReceiver = Union[ArrayType, DynamicArrayType]
ArgumentRule = Callable[[MethodCall, ArrayReceiver, Reporter, Optional["TypeValidator"]],
                        None]
ReturnRule = Callable[[ArrayReceiver, Optional["TypeValidator"]], Optional[Type]]


class Receiver(Enum):
    """Which array kinds a method takes as its receiver.

    The value is the `expected` text of CE2023, so the refusal and the rule are one
    statement. `ANY` never refuses and so never prints.
    """

    ANY = "any array"
    DYNAMIC = "dynamic array"
    BYTES = "u8[]"


class _InternedByTheCaller:
    """Marks a row whose answer is interned by the reader of this table.

    `get`, `first`, `last` and `pop` each answer `Maybe@(T)` and `index_of` answers
    `Maybe@(i32)`. Interning a `Maybe` needs the enum table and one owner, and
    `ArrayMethodInferrer` resolves all five before it reaches this table, so a rule here
    would be a second answer to a question already answered. The row still carries the
    marker, so no name sits in the table with no decision at all.
    """


INTERNED_BY_THE_CALLER = _InternedByTheCaller()


def _validate_element_argument(call: MethodCall, element_type: Type, reporter: Reporter,
                               validator: Optional['TypeValidator']) -> None:
    """The one check for "is this argument an element of this array?" (CE2006)."""
    if validator is None:
        return
    validator.validate_expression(call.args[0])
    arg_type = validator.infer_expression_type(call.args[0])
    if arg_type is None:
        return
    from .compatibility import types_compatible
    if not types_compatible(validator, arg_type, element_type):
        er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                index=1, expected=display_type(element_type), got=display_type(arg_type))


# --------------------------------------------------------------------- argument rules
#
# Each rule reads the arguments of ONE row, and runs only once the receiver kind and the
# argument count already hold, so no rule counts for itself.


def _accepts_anything(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
                      validator: Optional['TypeValidator']) -> None:
    """The arity is the whole rule."""


def _an_index(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
              validator: Optional['TypeValidator']) -> None:
    """One integer of any width."""
    if validator is None:
        return
    validator.validate_expression(call.args[0])
    arg_type = validator.infer_expression_type(call.args[0])
    if arg_type is not None and not is_integer_type(arg_type):
        er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                index=1, expected="integer type", got=display_type(arg_type))


def _an_index_the_size_holds(call: MethodCall, array_type: ArrayReceiver,
                             reporter: Reporter,
                             validator: Optional['TypeValidator']) -> None:
    """`get(i)`: an integer, and on a FIXED array one the declared size can hold."""
    _an_index(call, array_type, reporter, validator)
    if isinstance(array_type, ArrayType):
        validate_constant_array_index(call.args[0], array_type.size, reporter)


def _an_element(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
                validator: Optional['TypeValidator']) -> None:
    """One value of the element type."""
    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _an_element_to_store(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
                         validator: Optional['TypeValidator']) -> None:
    """`push(v)`: the element, with the stamp a bare enum variant needs before it is read."""
    if validator is None:
        return
    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, call.args[0], array_type.base_type)
    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _a_comparable_element(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
                          validator: Optional['TypeValidator']) -> None:
    """`contains(v)` and `index_of(v)`: one needle, of an element type that meets `==`.

    Equality is the CLOSED comparison set, asked through `has_builtin_equality` so this
    rule and the `==` operator's (CE2514) cannot drift apart. The element gate comes
    before the argument check: on a `Point[]` the useful answer is "a Point has no
    equality", not "the argument is the wrong type".
    """
    from .expressions import has_builtin_equality

    if not has_builtin_equality(array_type.base_type):
        er.emit(reporter, er.ERR.CE2100, call.loc, method=call.method,
                element=display_type(array_type.base_type))
        return
    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _reject_mismatched_source(call: MethodCall, array_type: ArrayReceiver,
                              reporter: Reporter,
                              validator: Optional['TypeValidator']) -> bool:
    """A bulk copy's source: an array, of the destination's element type.

    The source takes the receiver's element type before it is read, the stamp a `let u8[]`
    and a user function's `u8[]` parameter already give a `from([...])` literal (#544).
    Compared unstamped, the literal defaulted to `i32[]` and a correct call was CE2023
    (#576).
    """
    if validator is not None:
        from .propagation import propagate_types_to_value
        propagate_types_to_value(validator, call.args[0], array_type)
    source_type = validator.infer_expression_type(call.args[0]) if validator else None
    if source_type is None:
        return False
    source_type = deref_type(source_type)
    if not isinstance(source_type, (ArrayType, DynamicArrayType)):
        er.emit(reporter, er.ERR.CE2023, call.loc, method=call.method,
                expected="an array", got=display_type(source_type))
        return True
    if source_type.base_type != array_type.base_type:
        er.emit(reporter, er.ERR.CE2023, call.loc, method=call.method,
                expected=display_type(array_type), got=display_type(source_type))
        return True
    return False


def _reject_a_non_index(indices: Sequence[Expr], reporter: Reporter,
                        validator: Optional['TypeValidator']) -> None:
    """Every index position of a bulk copy is an i32."""
    for index in indices:
        index_type = validator.infer_expression_type(index) if validator else None
        if index_type is not None and index_type != BuiltinType.I32:
            er.emit(reporter, er.ERR.CE2002, index.loc,
                    got=display_type(index_type), expected=display_type(BuiltinType.I32))


def _a_source(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
              validator: Optional['TypeValidator']) -> None:
    """`extend(src)`: the source alone."""
    _reject_mismatched_source(call, array_type, reporter, validator)


def _a_source_and_a_range(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
                          validator: Optional['TypeValidator']) -> None:
    """`extend_range(src, start, count)`: the source, then the two indices behind it."""
    if _reject_mismatched_source(call, array_type, reporter, validator):
        return
    _reject_a_non_index(call.args[1:], reporter, validator)


def _a_range(call: MethodCall, array_type: ArrayReceiver, reporter: Reporter,
             validator: Optional['TypeValidator']) -> None:
    """`s(start, end)` and `ss(start, count)`: two indices and no source."""
    _reject_a_non_index(call.args, reporter, validator)


# ----------------------------------------------------------------------- return rules


def _answers(type_: Type) -> ReturnRule:
    """A method whose answer does not depend on its receiver."""
    def rule(array_type: ArrayReceiver,
             validator: Optional['TypeValidator']) -> Optional[Type]:
        return type_
    return rule


def _answers_when_dynamic(type_: Type) -> ReturnRule:
    """A method only a `T[]` can receive; a fixed one was already refused (CE2023)."""
    def rule(array_type: ArrayReceiver,
             validator: Optional['TypeValidator']) -> Optional[Type]:
        return type_ if isinstance(array_type, DynamicArrayType) else None
    return rule


def _the_byte_string(array_type: ArrayReceiver,
                     validator: Optional['TypeValidator']) -> Optional[Type]:
    """`u8[].to_string()`: the bytes as they stand."""
    if isinstance(array_type, DynamicArrayType) and array_type.base_type == BuiltinType.U8:
        return BuiltinType.STRING
    return None


def _the_checked_byte_string(array_type: ArrayReceiver,
                             validator: Optional['TypeValidator']) -> Optional[Type]:
    """`u8[].to_string_checked()`: `Result@(string, StdError)`, through the intern seam."""
    if validator is None:
        return None
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    std_error = validator.enum_table.by_name.get("StdError")
    return ensure_result_type_in_table(validator.enum_table, BuiltinType.STRING, std_error,
                                       struct_table=validator.struct_table.by_name)


def _an_element_iterator(array_type: ArrayReceiver,
                         validator: Optional['TypeValidator']) -> Optional[Type]:
    """`iter()`: an iterator over the element type."""
    return IteratorType(element_type=array_type.base_type)


def _the_receiver(array_type: ArrayReceiver,
                  validator: Optional['TypeValidator']) -> Optional[Type]:
    """`clone()`: a second value of the receiver's own type."""
    return array_type


def _a_fresh_array(array_type: ArrayReceiver,
                   validator: Optional['TypeValidator']) -> Optional[Type]:
    """A slice is a FRESH array, so a `T[]` whatever the source was: a fixed source gives
    a dynamic result, because the length is a run-time value.
    """
    return DynamicArrayType(base_type=array_type.base_type)


# ------------------------------------------------------------------------- the table


@dataclass(frozen=True)
class ArraySpec:
    """What one built-in array method accepts, and what it answers."""

    arity: int
    receiver: Receiver
    returns: Union[ReturnRule, _InternedByTheCaller]
    arguments: ArgumentRule = _accepts_anything
    # An in-place write. A constant is emitted as a read-only global, so a constant
    # receiver is refused before anything else is asked (CE2096).
    mutates: bool = False
    # The bulk-copy family reads the INTERNAL CE0023 for an arity fault where every other
    # row reads CE2009. Preserved, not corrected: #750 promised no change of behaviour.
    arity_code: ErrorMessage = er.ERR.CE2009


_ARRAY_METHODS: dict[str, ArraySpec] = {
    "len": ArraySpec(0, Receiver.ANY, _answers(BuiltinType.I32)),
    "get": ArraySpec(1, Receiver.ANY, INTERNED_BY_THE_CALLER,
                     arguments=_an_index_the_size_holds),
    "first": ArraySpec(0, Receiver.ANY, INTERNED_BY_THE_CALLER),
    "last": ArraySpec(0, Receiver.ANY, INTERNED_BY_THE_CALLER),
    "contains": ArraySpec(1, Receiver.ANY, _answers(BuiltinType.BOOL),
                          arguments=_a_comparable_element),
    "index_of": ArraySpec(1, Receiver.ANY, INTERNED_BY_THE_CALLER,
                          arguments=_a_comparable_element),
    # Only a buffer that can GROW or SHRINK takes these: a fixed array's length is part of
    # its type.
    "push": ArraySpec(1, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                      arguments=_an_element_to_store),
    "pop": ArraySpec(0, Receiver.DYNAMIC, INTERNED_BY_THE_CALLER),
    "clear": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                       mutates=True),
    "truncate": ArraySpec(1, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                          arguments=_an_index, mutates=True),
    "capacity": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.I32)),
    "destroy": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK)),
    "free": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK)),
    "iter": ArraySpec(0, Receiver.ANY, _an_element_iterator),
    "to_string": ArraySpec(0, Receiver.BYTES, _the_byte_string),
    "to_string_checked": ArraySpec(0, Receiver.BYTES, _the_checked_byte_string),
    # A fixed array is a value, but a fixed array OF owning elements (a `string[2]`) still
    # needs a way to take an independent copy, and `.clone()` is the only one the language
    # offers.
    "clone": ArraySpec(0, Receiver.ANY, _the_receiver),
    "hash": ArraySpec(0, Receiver.ANY, _answers(BuiltinType.U64)),
    "fill": ArraySpec(1, Receiver.ANY, _answers(BuiltinType.BLANK),
                      arguments=_an_element, mutates=True),
    "reverse": ArraySpec(0, Receiver.ANY, _answers(BuiltinType.BLANK), mutates=True),
    # The destination must be able to grow, so a fixed array is not a receiver here. It is
    # a legal SOURCE, and `.s()`/`.ss()` read either kind.
    "extend": ArraySpec(1, Receiver.DYNAMIC, _answers(BuiltinType.BLANK),
                        arguments=_a_source, mutates=True,
                        arity_code=er.ERR.CE0023),
    "extend_range": ArraySpec(3, Receiver.DYNAMIC, _answers(BuiltinType.BLANK),
                              arguments=_a_source_and_a_range, mutates=True,
                              arity_code=er.ERR.CE0023),
    "s": ArraySpec(2, Receiver.ANY, _a_fresh_array, arguments=_a_range,
                   arity_code=er.ERR.CE0023),
    "ss": ArraySpec(2, Receiver.ANY, _a_fresh_array, arguments=_a_range,
                    arity_code=er.ERR.CE0023),
}


# -------------------------------------------------------------- writing to a constant


def _names_an_unshadowed_constant(expr: Optional[Expr],
                                  validator: Optional['TypeValidator']) -> Optional[str]:
    """The constant at the ROOT of `expr`, or None. A local of the same name shadows it.

    The root, not the expression itself: `TABLE[i] := v` and `ORIGIN.x := v` both write
    into a constant, and `SEG.start.x := v` does so two levels down. Walking here is
    what keeps one seam answering for every writer.
    """
    if validator is None:
        return None
    walked = walk_place(expr, Step.MEMBER | Step.INDEX,
                        stop=lambda member: member.namespace_ref)
    ref = walked.stop
    if ref is not None:
        # `geo.SIZE`: the alias fold keeps the record's kind, so the answer is the
        # record's and not the alias name's.
        sig = validator.const_table.lookup(ref.name, ref.origin)
        return None if sig is None or sig.is_var else str(ref.name)
    if walked.name is None:
        return None
    name = walked.name.id
    if name in getattr(validator, 'variable_types', {}):
        return None
    # SCOPED, never the flat view: `by_name` holds one record per name over the whole
    # program and is first-wins, so it answered with another unit's declaration of the
    # same name -- a write to this unit's own constant was let through (#685).
    sig = validator.const_sig(name)
    # A unit variable is storage with an address, so a write reaches it (unit-storage.md).
    return None if sig is None or sig.is_var else name


def reject_write_to_constant(target: Optional[Expr], what: str, loc: Optional[Span],
                             reporter: Reporter,
                             validator: Optional['TypeValidator']) -> bool:
    """Reject a write that would reach a global constant (CE2096).

    Three writers ask here: an in-place method, an indexed assignment, and an assignment
    to a field. The store would land in .rodata, which is undefined behaviour rather
    than a diagnostic. A `poke self` method call is the fourth writer and does NOT come
    here: it takes an ADDRESS rather than storing, and CE2400 already says a constant
    has no frame slot to borrow.
    """
    name = _names_an_unshadowed_constant(target, validator)
    if name is None:
        return False
    er.emit(reporter, er.ERR.CE2096, loc, what=what, name=name)
    return True


# ------------------------------------------------------------- the three table readers


def _receiver_is_accepted(kind: Receiver, array_type: ArrayReceiver) -> bool:
    """Whether this receiver is one the row takes: any array, a `T[]`, or a `u8[]`."""
    if kind is Receiver.ANY:
        return True
    if not isinstance(array_type, DynamicArrayType):
        return False
    return kind is Receiver.DYNAMIC or array_type.base_type == BuiltinType.U8


def is_builtin_array_method(method_name: str) -> bool:
    """Whether a name is one of the built-in array methods."""
    return method_name in _ARRAY_METHODS


def validate_builtin_array_method(call: MethodCall, array_type: ArrayReceiver,
                                  reporter: Reporter,
                                  validator: Optional['TypeValidator'] = None) -> None:
    """Check one built-in array method call against its row.

    Four questions in a fixed order, because each answer is what makes the next one
    readable: an in-place method may not write a constant (CE2096), the receiver must be a
    kind the method takes (CE2023), the count must match (CE2009), and only then are the
    arguments themselves read.
    """
    spec = _ARRAY_METHODS.get(call.method)
    if spec is None:
        return

    if spec.mutates and reject_write_to_constant(
            call.receiver, f"call '{call.method}()' on", call.loc, reporter, validator):
        return

    if not _receiver_is_accepted(spec.receiver, array_type):
        er.emit(reporter, er.ERR.CE2023, call.loc, method=call.method,
                expected=spec.receiver.value, got=display_type(array_type))
        return

    if len(call.args) != spec.arity:
        # CE2009 names the method `name` and CE0023 names it `method`; `format_map` drops
        # whichever key the code's own text does not read.
        named = f"{display_type(array_type)}.{call.method}"
        er.emit(reporter, spec.arity_code, call.loc, name=named, method=named,
                expected=spec.arity, got=len(call.args))
        return

    spec.arguments(call, array_type, reporter, validator)


def get_builtin_array_method_return_type(
        method_name: str, array_type: ArrayReceiver,
        validator: Optional['TypeValidator'] = None) -> Optional[Type]:
    """The type a built-in array method answers, read from its row.

    A row marked `INTERNED_BY_THE_CALLER` answers nothing here: `ArrayMethodInferrer` owns
    the five `Maybe` answers, and resolves them before it reads this table.
    """
    spec = _ARRAY_METHODS.get(method_name)
    if spec is None or isinstance(spec.returns, _InternedByTheCaller):
        return None
    return spec.returns(array_type, validator)
