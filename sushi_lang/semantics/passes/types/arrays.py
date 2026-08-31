"""Built-in extension methods for array types (fixed and dynamic arrays)."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.typesys import Type, ArrayType, DynamicArrayType, BuiltinType, IteratorType, deref_type
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from .utils import validate_constant_array_index


# Array methods that mutate their receiver in place. A constant is emitted as a
# read-only global, so these cannot target one (CE2096).
_MUTATING_ARRAY_METHODS = frozenset({"fill", "reverse", "extend", "extend_range", "clear", "truncate"})


def _validate_element_argument(call: MethodCall, element_type: Type, reporter: Any,
                               validator: Any) -> None:
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


def _names_an_unshadowed_constant(expr: Any, validator: Any) -> Any:
    """The constant at the ROOT of `expr`, or None. A local of the same name shadows it.

    The root, not the expression itself: `TABLE[i] := v` and `ORIGIN.x := v` both write
    into a constant, and `SEG.start.x := v` does so two levels down. Walking here is
    what keeps one seam answering for every writer.
    """
    from sushi_lang.semantics.ast import IndexAccess, MemberAccess

    if validator is None:
        return None
    while isinstance(expr, (MemberAccess, IndexAccess)):
        expr = expr.receiver if isinstance(expr, MemberAccess) else expr.array
    if not isinstance(expr, Name):
        return None
    name = expr.id
    if name in getattr(validator, 'variable_types', {}):
        return None
    return name if name in validator.const_table.by_name else None


def reject_write_to_constant(target: Any, what: str, loc: Any,
                             reporter: Any, validator: Any) -> bool:
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


def _reject_mutation_of_constant(call: MethodCall, reporter: Any, validator: Any) -> bool:
    """Reject an in-place array method whose receiver is a global constant."""
    if call.method not in _MUTATING_ARRAY_METHODS:
        return False
    return reject_write_to_constant(call.receiver, f"call '{call.method}()' on",
                                    call.loc, reporter, validator)


def _is_integer_type(type_: Type) -> bool:
    """Check if a type is any integer type (signed or unsigned)."""
    return type_ in (
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
    )


def _validate_fixed_array_len(call: MethodCall, array_type: ArrayType, reporter: Any) -> None:
    """Validate len() method call on fixed arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.len", expected=0, got=len(call.args))


def _validate_fixed_array_get(call: MethodCall, array_type: ArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate get(index) method call on fixed arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.get", expected=1, got=len(call.args))
        return

    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and not _is_integer_type(arg_type):
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="integer type", got=display_type(arg_type))

    validate_constant_array_index(call.args[0], array_type.size, reporter)


def _validate_dynamic_array_len(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate len() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.len", expected=0, got=len(call.args))


def _validate_dynamic_array_capacity(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate capacity() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.capacity", expected=0, got=len(call.args))


def _validate_dynamic_array_get(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate get(index) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.get", expected=1, got=len(call.args))
        return

    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and not _is_integer_type(arg_type):
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="integer type", got=display_type(arg_type))


def _validate_dynamic_array_push(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate push(element) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.push", expected=1, got=len(call.args))
        return

    if validator:
        from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall
        propagate_enum_type_to_dotcall(validator, call.args[0], array_type.base_type)

        _validate_element_argument(call, array_type.base_type, reporter, validator)


def _validate_dynamic_array_pop(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate pop() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.pop", expected=0, got=len(call.args))


def _validate_dynamic_array_destroy(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate destroy() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.destroy", expected=0, got=len(call.args))


def _validate_dynamic_array_free(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate free() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.free", expected=0, got=len(call.args))


def _validate_array_iter(call: MethodCall, array_type: ArrayType | DynamicArrayType, reporter: Any) -> None:
    """Validate iter() method call on arrays (both fixed and dynamic)."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.iter", expected=0, got=len(call.args))


def _validate_byte_array_to_string(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate to_string() method call on u8[] byte arrays."""
    if array_type.base_type != BuiltinType.U8:
        er.emit(reporter, er.ERR.CE2023, call.loc,
               method="to_string", expected="u8[]", got=display_type(array_type))
        return

    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.to_string", expected=0, got=len(call.args))


def _validate_byte_array_to_string_checked(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate to_string_checked() method call on u8[] byte arrays."""
    if array_type.base_type != BuiltinType.U8:
        er.emit(reporter, er.ERR.CE2023, call.loc,
               method="to_string_checked", expected="u8[]", got=display_type(array_type))
        return

    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.to_string_checked", expected=0, got=len(call.args))


def _validate_array_clone(call: MethodCall, array_type: ArrayType | DynamicArrayType,
                          reporter: Any) -> None:
    """Validate clone() on a fixed or a dynamic array. It takes no arguments."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.clone", expected=0, got=len(call.args))


def _validate_fixed_array_fill(call: MethodCall, array_type: ArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate fill(value) method call on fixed arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.fill", expected=1, got=len(call.args))
        return

    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _validate_dynamic_array_fill(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate fill(value) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.fill", expected=1, got=len(call.args))
        return

    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _validate_array_search(call: MethodCall, array_type: ArrayType | DynamicArrayType,
                           reporter: Any, validator: Any) -> None:
    """`contains(v)` and `index_of(v)`: one needle, of an element type that meets `==`.

    Equality is the CLOSED comparison set, asked through `has_builtin_equality` so this
    rule and the `==` operator's (CE2514) cannot drift apart. The element gate comes
    before the argument check: on a `Point[]` the useful answer is "a Point has no
    equality", not "the argument is the wrong type".
    """
    from .expressions import has_builtin_equality

    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(array_type)}.{call.method}", expected=1,
                got=len(call.args))
        return

    if not has_builtin_equality(array_type.base_type):
        er.emit(reporter, er.ERR.CE2100, call.loc, method=call.method,
                element=display_type(array_type.base_type))
        return

    _validate_element_argument(call, array_type.base_type, reporter, validator)


def _validate_bulk_copy(call: MethodCall, array_type: Any, reporter: Any, validator: Any,
                        *, name: str, index_args: int) -> None:
    """`extend`, `extend_range` and `ss` share one shape: a source, and 0 or 2 indices.

    The source is a BORROW whose ELEMENT type must match, which is the only thing that
    separates this from an ordinary index check.
    """
    expected = index_args + (0 if name in ("s", "ss") else 1)
    if len(call.args) != expected:
        er.emit(reporter, er.ERR.CE0023, call.loc,
                method=f"{display_type(array_type)}.{name}", expected=expected,
                got=len(call.args))
        return

    if name not in ("s", "ss"):
        source_type = validator.infer_expression_type(call.args[0]) if validator else None
        if source_type is not None:
            source_type = deref_type(source_type)
            if not isinstance(source_type, (ArrayType, DynamicArrayType)):
                er.emit(reporter, er.ERR.CE2023, call.loc, method=name,
                        expected="an array", got=display_type(source_type))
                return
            if source_type.base_type != array_type.base_type:
                er.emit(reporter, er.ERR.CE2023, call.loc, method=name,
                        expected=display_type(array_type),
                        got=display_type(source_type))
                return

    for index in call.args[len(call.args) - index_args:]:
        index_type = validator.infer_expression_type(index) if validator else None
        if index_type is not None and index_type != BuiltinType.I32:
            er.emit(reporter, er.ERR.CE2002, index.loc,
                    got=display_type(index_type), expected=display_type(BuiltinType.I32))


def _validate_fixed_array_reverse(call: MethodCall, array_type: ArrayType, reporter: Any) -> None:
    """Validate reverse() method call on fixed arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.reverse", expected=0, got=len(call.args))


def _validate_dynamic_array_reverse(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate reverse() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(array_type)}.reverse", expected=0, got=len(call.args))


def is_builtin_array_method(method_name: str) -> bool:
    """Check if a method name is a built-in array method."""
    # Fixed array methods: len, get, first, last, contains, index_of, iter, hash, clone, fill, reverse, s, ss
    # Dynamic array methods: the same, plus push, pop, clear, truncate, capacity,
    #   destroy, free, extend, extend_range
    # u8[] specific methods: to_string
    return method_name in {"len", "get", "first", "last", "contains", "index_of", "push", "pop", "clear", "truncate", "capacity", "destroy", "free", "iter", "to_string", "to_string_checked", "clone", "hash", "fill", "reverse", "extend", "extend_range", "s", "ss"}


def validate_builtin_array_method(call: MethodCall, array_type: ArrayType | DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate built-in array method calls with optional validator for type checking."""
    method_name = call.method

    if _reject_mutation_of_constant(call, reporter, validator):
        return

    if method_name == "len":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_len(call, array_type, reporter)
        else:
            _validate_dynamic_array_len(call, array_type, reporter)

    elif method_name == "capacity":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="capacity", expected="dynamic array", got=display_type(array_type))
            return
        _validate_dynamic_array_capacity(call, array_type, reporter)

    elif method_name == "get":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_get(call, array_type, reporter, validator)
        else:
            _validate_dynamic_array_get(call, array_type, reporter, validator)

    elif method_name == "push":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="push", expected="dynamic array", got=display_type(array_type))
            return
        _validate_dynamic_array_push(call, array_type, reporter, validator)

    elif method_name == "pop":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="pop", expected="dynamic array", got=display_type(array_type))
            return
        _validate_dynamic_array_pop(call, array_type, reporter)

    elif method_name == "destroy":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="destroy", expected="dynamic array", got=display_type(array_type))
            return
        _validate_dynamic_array_destroy(call, array_type, reporter)

    elif method_name == "free":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="free", expected="dynamic array", got=display_type(array_type))
            return
        _validate_dynamic_array_free(call, array_type, reporter)

    elif method_name == "iter":
        _validate_array_iter(call, array_type, reporter)

    elif method_name == "to_string":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="to_string", expected="u8[]", got=display_type(array_type))
            return
        _validate_byte_array_to_string(call, array_type, reporter)

    elif method_name == "to_string_checked":
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="to_string_checked", expected="u8[]", got=display_type(array_type))
            return
        _validate_byte_array_to_string_checked(call, array_type, reporter)

    elif method_name == "clone":
        # clone() - available on both fixed and dynamic arrays. A fixed array is a value,
        # but a fixed array OF owning elements (a `string[2]`) still needs a way to take
        # an independent copy, and `.clone()` is the only one the language offers.
        _validate_array_clone(call, array_type, reporter)

    elif method_name == "hash":
        if call.args:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                   name=f"{display_type(array_type)}.hash", expected=0, got=len(call.args))

    elif method_name in ("clear", "truncate"):
        # Only a buffer that can SHRINK has these; a fixed array's length is its type,
        # the same reason `push` refuses one.
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                    method=method_name, expected="dynamic array",
                    got=display_type(array_type))
            return
        expected_args = 0 if method_name == "clear" else 1
        if len(call.args) != expected_args:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                    name=f"{display_type(array_type)}.{method_name}",
                    expected=expected_args, got=len(call.args))
            return
        if method_name == "truncate" and validator:
            validator.validate_expression(call.args[0])
            arg_type = validator.infer_expression_type(call.args[0])
            if arg_type is not None and not _is_integer_type(arg_type):
                er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                        index=1, expected="integer type", got=display_type(arg_type))

    elif method_name in ("contains", "index_of"):
        _validate_array_search(call, array_type, reporter, validator)

    elif method_name in ("first", "last"):
        # `get()` with the index built in, so the same shape on both array kinds: no
        # arguments, and the answer is Maybe@(T) (interned by `ArrayMethodInferrer`).
        if call.args:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                   name=f"{display_type(array_type)}.{method_name}", expected=0,
                   got=len(call.args))

    elif method_name == "fill":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_fill(call, array_type, reporter, validator)
        else:
            _validate_dynamic_array_fill(call, array_type, reporter, validator)

    elif method_name == "reverse":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_reverse(call, array_type, reporter)
        else:
            _validate_dynamic_array_reverse(call, array_type, reporter)

    elif method_name in ("extend", "extend_range"):
        # The destination must be able to GROW, so a fixed array is not a receiver: its
        # length is part of its type, the reason `.push()` refuses one too. A fixed array
        # is a legal SOURCE, and `.ss()` works on either.
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                    method=method_name, expected="dynamic array",
                    got=display_type(array_type))
            return
        _validate_bulk_copy(call, array_type, reporter, validator, name=method_name,
                            index_args=2 if method_name == "extend_range" else 0)

    elif method_name in ("s", "ss"):
        _validate_bulk_copy(call, array_type, reporter, validator, name=method_name,
                            index_args=2)


def get_builtin_array_method_return_type(method_name: str, array_type: ArrayType | DynamicArrayType) -> Type | None:
    """Get the return type of a built-in array method.

    `get`, `first`, `last`, `pop` and `index_of` are NOT here: each answers a `Maybe`,
    and interning that type is the caller's job (`ArrayMethodInferrer`), which resolves
    them before reaching this table.
    An entry here would be a second answer to a question already answered elsewhere.
    """
    if method_name == "len":
        return BuiltinType.I32
    elif method_name == "contains":
        return BuiltinType.BOOL
    elif method_name == "capacity":
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.I32
        return None
    elif method_name == "push":
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name in ("clear", "truncate"):
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "destroy":
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "free":
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "iter":
        return IteratorType(element_type=array_type.base_type)
    elif method_name == "to_string":
        if isinstance(array_type, DynamicArrayType) and array_type.base_type == BuiltinType.U8:
            return BuiltinType.STRING
        return None
    elif method_name == "clone":
        return array_type
    elif method_name == "hash":
        return BuiltinType.U64
    elif method_name == "fill":
        return BuiltinType.BLANK
    elif method_name == "reverse":
        return BuiltinType.BLANK
    elif method_name in ("extend", "extend_range"):
        return BuiltinType.BLANK
    elif method_name in ("s", "ss"):
        # A FRESH array, so a `T[]` whatever the source was: a fixed source gives a
        # dynamic result, because the length is a run-time value.
        return DynamicArrayType(base_type=array_type.base_type)
    return None
