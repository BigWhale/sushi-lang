"""Built-in extension methods for array types (fixed and dynamic arrays).

ONE table, `_ARRAY_METHODS`, holds the whole set. A row gives a method's arity, the
receiver kinds it accepts, the rule its arguments must meet, the type it answers and the
argument that a slot takes, so the membership predicate, the check of a call and the
return-type reader are readings of one table. Before #750 they were three hand-kept name
lists and twelve near-identical validators, and the lists had already drifted apart.
`tests/unit/test_array_method_table_is_total.py` is the gate, and it reads the BACKEND
dispatcher too, because its emitters are the fourth list of the same names.

One spelling is kept as it stood, because #750 promised no change of behaviour:
`extend`/`extend_range` answer a type for a fixed receiver although the check refuses
one there.

`check_array_method` is the arm of the family in the typecheck pass over SHIR
(`visit/calls.py:FAMILY_ARMS`). Its four questions come in a fixed order, because each
answer is what makes the next one readable: an in-place method may not write a constant
(CE2096), the receiver must be a kind the method takes (CE2023), the count must match
(CE2009), and only then are the arguments read. The call answers the type of its row
after each fault, as the type of a call does not depend on its arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Union

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import Span
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import effect_of
from sushi_lang.semantics.param_modes import BuiltinModes, ParamMode
from sushi_lang.semantics.passes.types.inference import int_literal_default
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.const_eval import is_integer_constant
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.exprs import ConstRef, IntConst, Lit, Unary
from sushi_lang.semantics.typesys import (ArrayType, BuiltinType, DynamicArrayType,
                                          Type, deref_type)
from sushi_lang.semantics.type_predicates import is_numeric_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.shir.exprs import ShirExpr


ArrayReceiver = Union[ArrayType, DynamicArrayType]
#: The check of the arguments of one row. It runs only once the receiver kind and the count
#: hold, so no rule counts for itself.
ArgumentRule = Callable[["TypeValidator", e.Call, ArrayReceiver], None]
ReturnRule = Callable[[ArrayReceiver, "TypeValidator"], Optional[Type]]


class Receiver(Enum):
    """Which array kinds a method takes as its receiver.

    The value is the `expected` text of CE2023, so the refusal and the rule are one
    statement. `ANY` never refuses and so never prints.
    """

    ANY = "any array"
    DYNAMIC = "dynamic array"
    BYTES = "u8[]"


# ------------------------------------------------------------------ an i32 position


# The positions that are not a method argument, as CE2121 names them.
ARRAY_INDEX = "an array index"
STRING_INDEX = "a string index"
REPEAT_COUNT = "a repeat count"
RANGE_BOUND = "a range bound"


def check_i32_operand(validator: 'TypeValidator', node: 'ShirExpr') -> Optional[Type]:
    """Check the value of an i32 position (an index, a count, a range bound) and answer its
    type. A bare literal has its default, which is i32 for a number. A bare byte literal
    (`a'x'`), also under a negation, takes i32 from the position. Any other value gets no
    type from the position, so a literal that an i32 cannot hold is CE2070."""
    from sushi_lang.semantics.passes.types import visit
    if not _is_byte_literal(node):
        return visit.check_expr(validator, node, None)
    _reject_negated_byte(validator, node)
    return visit.check_expr(validator, node, BuiltinType.I32)


def _reject_negated_byte(validator: 'TypeValidator', node: 'ShirExpr') -> None:
    """CE2073 at the negation of a byte literal whose value is below zero. A byte literal
    is a `u8` value, so its negation is out of range for `u8`, also in a position that gives
    it i32."""
    if not (isinstance(node, Unary) and node.op == "neg" and isinstance(node.operand, Lit)
            and isinstance(node.operand.value, IntConst)):
        return
    const = node.operand.value
    value = -const.value
    if value < 0:
        er.emit(validator.reporter, er.ERR.CE2073, node.loc, literal=str(value),
                type=int_literal_default(const).value)


def _is_byte_literal(node: 'ShirExpr') -> bool:
    while isinstance(node, Unary) and node.op in ("neg", "~"):
        node = node.operand
    return (isinstance(node, Lit) and isinstance(node.value, IntConst)
            and node.value.byte_spelling is not None)


def reject_non_i32(validator: 'TypeValidator', node: 'ShirExpr', got: Optional[Type], *,
                   position: Optional[str] = None, argument: Optional[int] = None,
                   span: Optional[Span] = None) -> bool:
    """The one rule for an index, a count and a range bound: the value is an i32 (#870).

    `got` is the type of `node` (`check_i32_operand`). Any type other than i32 is refused,
    and there is no implicit widening: the backend used to zero-extend a narrow value, so
    `-1 as i8` counted 255. A method argument reads CE2006 at its `argument` position;
    every other position reads CE2121, which names the `position` the caller gives. `span`
    is where it stands when it is not the span of `node` (a marked argument).
    Answers whether the value was refused.
    """
    if got is None or got == BuiltinType.I32:
        return False
    loc = node.loc if span is None else span
    if argument is None:
        report = er.emit_with(validator.reporter, er.ERR.CE2121, loc,
                              position=position, got=display_type(got))
    else:
        report = er.emit_with(validator.reporter, er.ERR.CE2006, loc, index=argument,
                              expected=display_type(BuiltinType.I32), got=display_type(got))
    if is_numeric_type(got):
        report = report.help("convert it with 'as i32'")
    report.emit()
    return True


def reject_index_out_of_range(validator: 'TypeValidator', index: 'ShirExpr',
                              size: int) -> None:
    """CE2056 for an index that the compiler can read and that is negative, CE2012 for one
    past the end of a fixed array of `size` elements."""
    read = validator.body_folder().scalar(index, BuiltinType.I32)
    if read is None or not is_integer_constant(read):
        return
    value = int(read.value)
    if value < 0:
        er.emit(validator.reporter, er.ERR.CE2056, index.loc, index=value)
    elif value >= size:
        er.emit(validator.reporter, er.ERR.CE2012, index.loc, index=value, size=size)


# --------------------------------------------------------------------- argument rules


def _accepts_anything(validator: 'TypeValidator', node: e.Call,
                      array_type: ArrayReceiver) -> None:
    """The arity is the whole rule."""


def _check_an_index(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver,
                    position: int, *, bounded: bool = False) -> None:
    """One index or count, an i32 (CE2006 at the argument). A `bounded` index of a FIXED
    array (`get(i)`, `swap(i, j)`) is one that the declared size can hold, as for `arr[i]`.
    A start or a count of a range clamps, so it has no bound."""
    from .arguments import marked_type
    arg = node.args[position]
    got = marked_type(arg, check_i32_operand(validator, arg.value))
    if reject_non_i32(validator, arg.value, got, argument=position + 1):
        return
    if bounded and isinstance(array_type, ArrayType):
        reject_index_out_of_range(validator, arg.value, array_type.size)


def _an_index(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver) -> None:
    """`get(i)`, `remove(i)`, `truncate(n)`: one i32 position."""
    _check_an_index(validator, node, array_type, 0, bounded=True)


def _two_indexes(validator: 'TypeValidator', node: e.Call,
                 array_type: ArrayReceiver) -> None:
    """`swap(i, j)`: each index is read as the index of `arr[i]` is."""
    for position in (0, 1):
        _check_an_index(validator, node, array_type, position, bounded=True)


def _a_range(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver) -> None:
    """`s(start, end)`, `ss(start, count)` and `extend_back(dist, count)`: two i32
    positions and no source."""
    for position in (0, 1):
        _check_an_index(validator, node, array_type, position)


def _check_an_element(validator: 'TypeValidator', node: e.Call, element_type: Type,
                      position: int, *, typed_by_position: bool = True) -> None:
    """The one check for "is this argument an element of this array?" (CE2006). A bare
    literal takes the element type of its position, except where the row says otherwise."""
    from .arguments import check_argument
    check_argument(validator, node_name(node), node.args[position], position + 1,
                   element_type, mismatch_code=er.ERR.CE2006,
                   typed_by_position=typed_by_position)


def _an_element(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver) -> None:
    """`fill(v)`: one value of the element type, read with no type from its position."""
    _check_an_element(validator, node, array_type.base_type, 0, typed_by_position=False)


def _a_length_and_an_element(validator: 'TypeValidator', node: e.Call,
                             array_type: ArrayReceiver) -> None:
    """`resize(n, v)`: an i32 length, then the value of `fill(v)`, a borrowed element."""
    _check_an_index(validator, node, array_type, 0, bounded=True)
    _check_an_element(validator, node, array_type.base_type, 1, typed_by_position=False)


def _a_numeric_element(validator: 'TypeValidator', node: e.Call,
                       array_type: ArrayReceiver) -> None:
    """`min()`, `max()` and `add_up()`: no argument, and an element that `<` and `+` take
    as a number (CE2128)."""
    element = array_type.base_type
    if not is_numeric_type(element):
        er.emit(validator.reporter, er.ERR.CE2128, node.loc, method=node_name(node),
                element=display_type(element))


def _an_element_to_store(validator: 'TypeValidator', node: e.Call,
                         array_type: ArrayReceiver) -> None:
    """`push(v)`: the element that the slot takes."""
    _check_an_element(validator, node, array_type.base_type, 0)


def _an_index_and_an_element_to_store(validator: 'TypeValidator', node: e.Call,
                                      array_type: ArrayReceiver) -> None:
    """`insert(i, v)`: an i32 index, then the element that the slot takes."""
    _check_an_index(validator, node, array_type, 0, bounded=True)
    _check_an_element(validator, node, array_type.base_type, 1)


def check_search_needle(validator: 'TypeValidator', node: e.Call,
                        element_type: Type) -> bool:
    """The needle of a search (`contains`, `index_of`, `index_of_from`), on every container.

    Equality is the `==` operator's rule (CE2514), asked through `has_equality` so the
    two cannot drift apart: a primitive in the closed set, or a struct or an enum with
    a derived or implemented `Eq`. The element gate comes before the argument check: on
    a `Handler[]` the useful answer is "a Handler has no equality", not "the argument is
    the wrong type". The needle takes the element type of its position, as a `push`
    element does, so a bare literal or a bare `Maybe.None()` is typed by the position.
    Answers whether the call was refused.
    """
    if _refuses_an_uncomparable_element(validator, node, element_type):
        return True
    _check_an_element(validator, node, element_type, 0)
    return False


def _refuses_an_uncomparable_element(validator: 'TypeValidator', node: e.Call,
                                     element_type: Type) -> bool:
    """The element gate of every method that compares elements (CE2100)."""
    from .expressions import has_equality

    refused: list = []
    comparable = has_equality(validator, element_type, refused)
    if not comparable:
        _reject_uncomparable_element(validator.reporter, node, element_type, refused)
    return not comparable


def _a_comparable_element(validator: 'TypeValidator', node: e.Call,
                          array_type: ArrayReceiver) -> None:
    """`contains(v)` and `index_of(v)`: one needle, of an element type that meets `==`."""
    check_search_needle(validator, node, array_type.base_type)


def _a_comparable_element_and_a_start(validator: 'TypeValidator', node: e.Call,
                                      array_type: ArrayReceiver) -> None:
    """`index_of_from(v, start)`: the needle, then the start, an i32 position."""
    if check_search_needle(validator, node, array_type.base_type):
        return
    _check_an_index(validator, node, array_type, 1)


def _reject_uncomparable_element(reporter: Reporter, node: e.Call, element,
                                 refused: list) -> None:
    """CE2100. An array element has its own reason and escape: an array has no top-level
    `==` (CE2514), and a struct that holds the row takes a derived `Eq` (#1116)."""
    shown = display_type(element)
    method = node_name(node)
    if not isinstance(element, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.generics.opaque import explain_unpromised
        explain_unpromised(er.emit_with(reporter, er.ERR.CE2100, node.loc,
                                        method=method, reason=f"'{shown}' has none"),
                           "Eq", refused).emit()
        return
    er.emit_with(reporter, er.ERR.CE2100, node.loc, method=method,
                 reason=f"'{shown}' is an array, and an array has no '==' at the top "
                        "level").help(
        f"put the row in a struct ('struct Row: {shown} cells'), which takes a derived "
        f"'Eq', and search a 'Row[]'").emit()


def _reject_mismatched_source(validator: 'TypeValidator', node: e.Call,
                              array_type: ArrayReceiver, position: int = 0) -> bool:
    """A bulk copy's source: an array, of the destination's element type (CE2023).

    The source takes the receiver's type as the type of its position, so a `from([...])`
    literal has the element type of the receiver (#544). Read with no type, the literal
    defaulted to `i32[]` and a correct call was CE2023 (#576). Answers whether it refused.
    """
    from sushi_lang.semantics.passes.types import visit
    source_type = visit.check_expr(validator, node.args[position].value, array_type)
    if source_type is None:
        return False
    source_type = deref_type(source_type)
    method = node_name(node)
    if not isinstance(source_type, (ArrayType, DynamicArrayType)):
        er.emit(validator.reporter, er.ERR.CE2023, node.loc, part="argument", method=method,
                expected="an array", got=display_type(source_type))
        return True
    if source_type.base_type != array_type.base_type:
        er.emit(validator.reporter, er.ERR.CE2023, node.loc, part="argument", method=method,
                expected=display_type(array_type), got=display_type(source_type))
        return True
    return False


def _a_source(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver) -> None:
    """`extend(src)`: the source alone."""
    _reject_mismatched_source(validator, node, array_type)


def _a_source_and_a_range(validator: 'TypeValidator', node: e.Call,
                          array_type: ArrayReceiver) -> None:
    """`extend_range(src, start, count)`: the source, then the two indices behind it."""
    if _reject_mismatched_source(validator, node, array_type):
        return
    for position in (1, 2):
        _check_an_index(validator, node, array_type, position)


def _a_comparable_source(validator: 'TypeValidator', node: e.Call,
                         array_type: ArrayReceiver) -> None:
    """`starts_with(prefix)`: an array of the element type, and the element meets `==`."""
    if _refuses_an_uncomparable_element(validator, node, array_type.base_type):
        return
    _reject_mismatched_source(validator, node, array_type)


def _a_start_and_a_comparable_source(validator: 'TypeValidator', node: e.Call,
                                     array_type: ArrayReceiver) -> None:
    """`eq_range(start, other)`: an i32 start, then an array of the element type."""
    if _refuses_an_uncomparable_element(validator, node, array_type.base_type):
        return
    _check_an_index(validator, node, array_type, 0)
    _reject_mismatched_source(validator, node, array_type, position=1)


def _a_string(validator: 'TypeValidator', node: e.Call, array_type: ArrayReceiver) -> None:
    """`extend_str(s)`: the source is a string, and its bytes are the elements."""
    from sushi_lang.semantics.passes.types import visit
    source_type = visit.check_expr(validator, node.args[0].value)
    if source_type is None:
        return
    source_type = deref_type(source_type)
    if source_type == BuiltinType.STRING:
        return
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2023, node.loc, part="argument",
                              method=node_name(node),
                              expected=display_type(BuiltinType.STRING),
                              got=display_type(source_type))
    if source_type == array_type:
        diagnostic.help(f"to append a `{display_type(source_type)}`, write `buf.extend(...)`")


def node_name(node: e.Call) -> str:
    """The written name of the method that the call `node` calls."""
    target = node.target
    if not isinstance(target, e.Dotted):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="an array method call has no dotted target")
    return target.name


# ----------------------------------------------------------------------- return rules


def _answers(type_: Type) -> ReturnRule:
    """A method whose answer does not depend on its receiver."""
    def rule(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
        return type_
    return rule


def _answers_when_dynamic(type_: Type) -> ReturnRule:
    """A method only a `T[]` can receive; a fixed one was already refused (CE2023)."""
    def rule(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
        return type_ if isinstance(array_type, DynamicArrayType) else None
    return rule


def _maybe(validator: 'TypeValidator', payload: Type) -> Type:
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
    return ensure_maybe_type_in_table(validator.enum_table, payload,
                                      struct_table=validator.struct_table.by_name)


def _a_maybe_element(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
    """`get`, `first` and `last` READ, and `pop` and `remove` REMOVE, but all five answer
    "there is no such element" the same way, so each is `Maybe@(T)`, as `List@(T)` is. A
    bare `T` had to invent a value for the empty case (#377). `min` and `max` have the same
    empty case."""
    return _maybe(validator, array_type.base_type)


def _a_maybe_index(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
    """`index_of` and `index_of_from` answer WHERE, so the `Maybe` carries the index and not
    the element."""
    return _maybe(validator, BuiltinType.I32)


def _the_byte_string(array_type: ArrayReceiver,
                     validator: 'TypeValidator') -> Optional[Type]:
    """`u8[].to_string()`: the bytes as they stand."""
    if isinstance(array_type, DynamicArrayType) and array_type.base_type == BuiltinType.U8:
        return BuiltinType.STRING
    return None


def _std_error_result(validator: 'TypeValidator', ok: Type) -> Type:
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    std_error = validator.enum_table.by_name.get("StdError")
    return ensure_result_type_in_table(validator.enum_table, ok, std_error,
                                       struct_table=validator.struct_table.by_name)


def _the_checked_byte_string(array_type: ArrayReceiver,
                             validator: 'TypeValidator') -> Optional[Type]:
    """`u8[].to_string_checked()`: `Result@(string, StdError)`, through the intern seam."""
    return _std_error_result(validator, BuiltinType.STRING)


def _the_checked_insert(array_type: ArrayReceiver,
                        validator: 'TypeValidator') -> Optional[Type]:
    """`T[].insert(i, v)`: `Result@(~, StdError)`, through the intern seam."""
    if not isinstance(array_type, DynamicArrayType):
        return None
    return _std_error_result(validator, BuiltinType.BLANK)


def _the_element(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
    """`add_up()`: one value of the element type."""
    return array_type.base_type


def _an_element_iterator(array_type: ArrayReceiver,
                         validator: 'TypeValidator') -> Optional[Type]:
    """`iter()`: an iterator that lends each element, `Iterator@(peek T)`."""
    from .utils import producer_iterator
    return producer_iterator(validator, _the_element(array_type, validator))


def _the_receiver(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
    """`clone()`: a second value of the receiver's own type."""
    return array_type


def _a_fresh_array(array_type: ArrayReceiver, validator: 'TypeValidator') -> Optional[Type]:
    """A slice is a FRESH array, so a `T[]` whatever the source was: a fixed source gives
    a dynamic result, because the length is a run-time value.
    """
    return DynamicArrayType(base_type=array_type.base_type)


# ------------------------------------------------------------------------- the table


@dataclass(frozen=True)
class ArraySpec:
    """What one built-in array method accepts, and what it answers. `slot` is the position
    of the argument that a slot of the array takes by position (`push`, `insert`)."""

    arity: int
    receiver: Receiver
    returns: ReturnRule
    arguments: ArgumentRule = _accepts_anything
    slot: Optional[int] = None


_ARRAY_METHODS: dict[str, ArraySpec] = {
    "len": ArraySpec(0, Receiver.ANY, _answers(BuiltinType.I32)),
    "get": ArraySpec(1, Receiver.ANY, _a_maybe_element, arguments=_an_index),
    "first": ArraySpec(0, Receiver.ANY, _a_maybe_element),
    "last": ArraySpec(0, Receiver.ANY, _a_maybe_element),
    "contains": ArraySpec(1, Receiver.ANY, _answers(BuiltinType.BOOL),
                          arguments=_a_comparable_element),
    "index_of": ArraySpec(1, Receiver.ANY, _a_maybe_index, arguments=_a_comparable_element),
    "index_of_from": ArraySpec(2, Receiver.ANY, _a_maybe_index,
                               arguments=_a_comparable_element_and_a_start),
    # Range equality: a borrowed array of the element type, fixed or dynamic, as the
    # source of `extend`.
    "starts_with": ArraySpec(1, Receiver.ANY, _answers(BuiltinType.BOOL),
                             arguments=_a_comparable_source),
    "eq_range": ArraySpec(2, Receiver.ANY, _answers(BuiltinType.BOOL),
                          arguments=_a_start_and_a_comparable_source),
    # Only a buffer that can GROW or SHRINK takes these: a fixed array's length is part of
    # its type.
    "push": ArraySpec(1, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                      arguments=_an_element_to_store, slot=0),
    "pop": ArraySpec(0, Receiver.DYNAMIC, _a_maybe_element),
    "clear": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK)),
    "truncate": ArraySpec(1, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                          arguments=_an_index),
    "capacity": ArraySpec(0, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.I32)),
    # The `List@(T)` contract (ruling R3): `insert` answers `Result@(~, StdError)`, and
    # `remove` answers `Maybe@(T)`.
    "insert": ArraySpec(2, Receiver.DYNAMIC, _the_checked_insert,
                        arguments=_an_index_and_an_element_to_store, slot=1),
    "remove": ArraySpec(1, Receiver.DYNAMIC, _a_maybe_element, arguments=_an_index),
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
    "fill": ArraySpec(1, Receiver.ANY, _answers(BuiltinType.BLANK), arguments=_an_element),
    "reverse": ArraySpec(0, Receiver.ANY, _answers(BuiltinType.BLANK)),
    "swap": ArraySpec(2, Receiver.ANY, _answers(BuiltinType.BLANK), arguments=_two_indexes),
    "resize": ArraySpec(2, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                        arguments=_a_length_and_an_element),
    # The numeric reductions. `min` and `max` answer `Maybe@(T)`, because an empty array
    # has no such element; `add_up` answers zero there.
    "min": ArraySpec(0, Receiver.ANY, _a_maybe_element, arguments=_a_numeric_element),
    "max": ArraySpec(0, Receiver.ANY, _a_maybe_element, arguments=_a_numeric_element),
    "add_up": ArraySpec(0, Receiver.ANY, _the_element, arguments=_a_numeric_element),
    # The destination must be able to grow, so a fixed array is not a receiver here. It is
    # a legal SOURCE, and `.s()`/`.ss()` read either kind.
    "extend": ArraySpec(1, Receiver.DYNAMIC, _answers(BuiltinType.BLANK),
                        arguments=_a_source),
    "extend_range": ArraySpec(3, Receiver.DYNAMIC, _answers(BuiltinType.BLANK),
                              arguments=_a_source_and_a_range),
    "extend_str": ArraySpec(1, Receiver.BYTES, _answers_when_dynamic(BuiltinType.BLANK),
                            arguments=_a_string),
    "extend_back": ArraySpec(2, Receiver.DYNAMIC, _answers_when_dynamic(BuiltinType.BLANK),
                             arguments=_a_range),
    "s": ArraySpec(2, Receiver.ANY, _a_fresh_array, arguments=_a_range),
    "ss": ArraySpec(2, Receiver.ANY, _a_fresh_array, arguments=_a_range),
}

#: The argument count of each array method (CE2009, `reject_builtin_miscount`).
ARRAY_METHOD_ARITY: Mapping[str, int] = MappingProxyType(
    {name: spec.arity for name, spec in _ARRAY_METHODS.items()})

#: The parameter modes of the array methods (#1173). `push` and `insert` store into a
#: slot, which takes ownership by position; every other parameter borrows.
ARRAY_METHOD_MODES = BuiltinModes(slots=frozenset(
    name for name, spec in _ARRAY_METHODS.items() if spec.slot is not None))


# -------------------------------------------------------------- writing to a constant


def reject_write_into_constant(validator: 'TypeValidator', place: 'ShirExpr', what: str,
                               loc: Optional[Span]) -> bool:
    """CE2096 when the root of the SHIR place `place` is a constant, at any depth: a field
    write, an element write and an in-place method. The store would land in .rodata,
    which is undefined behaviour rather than a diagnostic. The scope pass resolved the
    name, so a local of the same name is a `LocalRef` and not a constant. A `poke self`
    method call does NOT come here: it takes an ADDRESS rather than storing, and CE2400
    already says a constant has no frame slot to borrow."""
    root = walk_place(place, Step.MEMBER | Step.INDEX).node
    if not isinstance(root, ConstRef):
        return False
    name = validator.decl_name(root.const_id).rpartition(".")[2]
    er.emit(validator.reporter, er.ERR.CE2096, loc, what=what, name=name)
    return True


# ------------------------------------------------------------------- the table readers


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


def check_array_method(validator: 'TypeValidator', node: e.Call, receiver: Type, name: str,
                       _family: object) -> Optional[Type]:
    """The arm of the array family: check one call against its row, give each argument its
    convention, and answer the type of the row (see the module docstring for the order).

    A slot argument (`push`, the value of `insert`) is `Owned`: the slot takes it by
    position, so no marker is compared. Every other argument borrows (`Guaranteed`), and a
    `nom` on it is CE2427.
    """
    array_type = receiver
    if not isinstance(array_type, (ArrayType, DynamicArrayType)):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="the array family answered a receiver that is no array")
    spec = _ARRAY_METHODS[name]
    answer = array_method_return_type(validator, array_type, name)
    recv = node.target.recv if isinstance(node.target, e.Dotted) else None
    if (effect_of(name).mutates and recv is not None
            and reject_write_into_constant(validator, recv, f"call '{name}()' on", node.loc)):
        return answer
    from .calls.methods import reject_uncloneable
    if name == "clone" and reject_uncloneable(validator, node, array_type):
        return answer
    if not _receiver_is_accepted(spec.receiver, array_type):
        er.emit(validator.reporter, er.ERR.CE2023, node.loc, part="receiver", method=name,
                expected=spec.receiver.value, got=display_type(array_type))
        return answer
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               f"{display_type(array_type)}.{name}", ARRAY_METHOD_ARITY):
        return answer
    spec.arguments(validator, node, array_type)
    _record_conventions(validator, node, spec)
    return answer


def _record_conventions(validator: 'TypeValidator', node: e.Call, spec: ArraySpec) -> None:
    from .arguments import Parameters, record_conventions
    count = len(node.args)
    declared = Parameters(node.shir_id, (ParamMode.BORROW,) * count,
                          [f"#{position}" for position in range(1, count + 1)])
    record_conventions(validator, declared, node.args,
                       slots=() if spec.slot is None else (spec.slot,))


def array_method_return_type(validator: 'TypeValidator', array_type: ArrayReceiver,
                             method_name: str) -> Optional[Type]:
    """The type a built-in array method answers, read from its row: the one answer of the
    typecheck arm and of the inference of the early passes."""
    spec = _ARRAY_METHODS.get(method_name)
    return None if spec is None else spec.returns(deref_type(array_type), validator)
