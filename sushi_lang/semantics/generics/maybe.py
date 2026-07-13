"""Validation and table-building for the built-in Maybe<T> methods.

The ir-free half of the former ``backend/generics/maybe.py``: method recognition,
Pass-2 argument validation, and on-the-fly ``Maybe<T>`` enum-table construction.
LLVM emission stays in the backend module. Keeping this in ``semantics`` is what
lets Pass 2 validate Maybe methods without importing the backend.
"""
from typing import Any, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type, BuiltinType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def is_builtin_maybe_method(method_name: str) -> bool:
    """Return True if ``method_name`` is a recognized Maybe<T> method."""
    return method_name in ("is_some", "is_none", "realise", "expect")


def validate_maybe_method_with_validator(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a Maybe<T> method call, routing on the method name."""
    if call.method == "is_some":
        _validate_maybe_is_some(call, maybe_type, reporter)
    elif call.method == "is_none":
        _validate_maybe_is_none(call, maybe_type, reporter)
    elif call.method == "realise":
        _validate_maybe_realise(call, maybe_type, reporter, validator)
    elif call.method == "expect":
        _validate_maybe_expect(call, maybe_type, reporter, validator)
    else:
        # Unknown method - should not happen if is_builtin_maybe_method was called first
        raise_internal_error("CE0094", method=call.method)


def _validate_maybe_is_some(call: MethodCall, maybe_type: EnumType, reporter: Any) -> None:
    """Validate Maybe<T>.is_some() takes no arguments."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_some", expected=0, got=len(call.args))


def _validate_maybe_is_none(call: MethodCall, maybe_type: EnumType, reporter: Any) -> None:
    """Validate Maybe<T>.is_none() takes no arguments."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_none", expected=0, got=len(call.args))


def _validate_maybe_realise(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate Maybe<T>.realise(default): one arg whose type matches T.

    Emits CE2016 on wrong arity, CE2503 on a type mismatch, CE2506 on blank T.
    """
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="realise", expected=1, got=len(call.args))
        return

    # Extract T from Maybe<T> via the Some variant's associated type.
    some_variant = maybe_type.get_variant("Some")
    if some_variant is None:
        raise_internal_error("CE0092", enum=maybe_type.name)

    if len(some_variant.associated_types) != 1:
        raise_internal_error("CE0093", got=len(some_variant.associated_types))

    t_type = some_variant.associated_types[0]

    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    default_arg = call.args[0]

    # Propagate expected type to DotCall nodes so maybe.realise(Result.Ok(...)) works.
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall
    propagate_enum_type_to_dotcall(validator, default_arg, t_type)

    validator.validate_expression(default_arg)

    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
                expected=str(t_type), got=str(arg_type))


def _validate_maybe_expect(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate Maybe<T>.expect(message): one string argument.

    Emits CE2016 on wrong arity, CE2503 if the message is not a string.
    """
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="expect", expected=1, got=len(call.args))
        return

    message_arg = call.args[0]

    validator.validate_expression(message_arg)

    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
                expected="string", got=str(arg_type))


def ensure_maybe_type_in_table(enum_table: Any, value_type: Type) -> Optional[EnumType]:
    """Ensure ``Maybe<value_type>`` exists in ``enum_table``, creating it if needed.

    Works with just an enum table so both semantic analysis and codegen can call it.

    ``generic_base`` / ``generic_args`` are populated because ``unify.py`` reads them to match
    a monomorphized generic against a ``Maybe<T>`` parameter. Without them an on-demand Maybe
    (the return of ``List.get`` and friends) carried no generic metadata, so passing one to
    ``fn f<T>(Maybe<T> m)`` died with CE2060 -- while an annotated ``let Maybe<i32> m`` worked,
    because that path goes through the monomorphizer, which does set them.
    """
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo
    from sushi_lang.semantics.generics.hashing import can_enum_be_hashed, register_enum_hash_method

    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    maybe_enum_name = f"Maybe<{type_str}>"

    if maybe_enum_name in enum_table.by_name:
        return enum_table.by_name[maybe_enum_name]

    some_variant = EnumVariantInfo(name="Some", associated_types=(value_type,))
    none_variant = EnumVariantInfo(name="None", associated_types=())

    maybe_enum = EnumType(
        name=maybe_enum_name,
        variants=(some_variant, none_variant),
        generic_base="Maybe",
        generic_args=(value_type,),
    )

    enum_table.by_name[maybe_enum_name] = maybe_enum
    enum_table.order.append(maybe_enum_name)

    # Register the auto-derived hash() for the on-demand type (mirrors Pass 1.8), matching
    # what the Result twin has always done.
    can_hash, _ = can_enum_be_hashed(maybe_enum)
    if can_hash:
        register_enum_hash_method(maybe_enum)

    return maybe_enum
