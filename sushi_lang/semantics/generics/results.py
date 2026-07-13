"""Validation and table-building for the built-in Result<T, E> methods.

The ir-free half of the former ``backend/generics/results.py``: method
recognition, Pass-2 argument validation, and on-the-fly ``Result<T, E>``
enum-table construction. LLVM emission stays in the backend module.
"""
from typing import Any, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.semantics.generics.hashing import can_enum_be_hashed, register_enum_hash_method
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def is_builtin_result_method(method_name: str) -> bool:
    """Check if a method name is a builtin Result<T, E> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Result<T, E> method, False otherwise.
    """
    return method_name in ("is_ok", "is_err", "realise", "expect", "err")


def validate_result_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    # CRITICAL: Annotate the MethodCall with the resolved Result<T, E> type
    # This allows the backend to use the correct type during code generation
    # instead of relying on unreliable LLVM type matching
    call.resolved_enum_type = result_type

    if call.method == "is_ok":
        _validate_result_is_ok(call, result_type, reporter)
    elif call.method == "is_err":
        _validate_result_is_err(call, result_type, reporter)
    elif call.method == "realise":
        validate_result_realise_method_with_validator(call, result_type, reporter, validator)
    elif call.method == "expect":
        _validate_result_expect(call, result_type, reporter, validator)
    elif call.method == "err":
        _validate_result_err(call, result_type, reporter)
    else:
        # Unknown method - should not happen if is_builtin_result_method was called first
        raise_internal_error("CE0094", method=call.method)


def _validate_result_is_ok(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.is_ok() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_ok", expected=0, got=len(call.args))


def _validate_result_is_err(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.is_err() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_err", expected=0, got=len(call.args))


def _validate_result_err(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.err() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="err", expected=0, got=len(call.args))


def _validate_result_expect(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E>.expect(message) method call.

    Validates that:
    1. Exactly one argument is provided (the error message)
    2. The message is a string type

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.

    Emits:
        CE2016: If argument count is not exactly 1
        CE2503: If message is not a string
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="expect", expected=1, got=len(call.args))
        return

    # Validate the message argument is a string
    message_arg = call.args[0]

    # First validate the argument expression
    validator.validate_expression(message_arg)

    # Then check it's a string
    from sushi_lang.semantics.typesys import BuiltinType
    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
               expected="string", got=str(arg_type))


def validate_result_realise_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T>.realise(default) method call.

    Validates that:
    1. Exactly one argument is provided (the default value)
    2. The default value type matches T in Result<T>

    Args:
        call: The method call AST node.
        result_type: The Result<T> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.

    Emits:
        CE2502: If argument count is not exactly 1
        CE2503: If default value type doesn't match T
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2502, call.loc, got=len(call.args))
        return

    # Extract T from Result<T> by getting the Ok variant's associated type
    # Result<T> has two variants: Ok(T) and Err()
    # We need to find the Ok variant and extract its associated type
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        # This shouldn't happen for a valid Result<T> enum
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        # Ok variant should have exactly one associated type (T)
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    t_type = ok_variant.associated_types[0]

    # Check if T is blank type - if's an error
    from sushi_lang.semantics.typesys import BuiltinType
    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    # Validate the default argument's type matches T
    default_arg = call.args[0]

    # Resolve GenericTypeRef to concrete type for propagation
    # This handles cases like HashMap<i32, string> which may be stored as GenericTypeRef
    from sushi_lang.semantics.type_resolution import TypeResolver
    from sushi_lang.semantics.typesys import StructType
    type_resolver = TypeResolver(validator.struct_table.by_name, validator.enum_table.by_name)
    resolved_t_type = type_resolver.resolve_generic_type_ref(t_type)

    # Propagate expected type to DotCall nodes for generic enums (before validation)
    # This allows result.realise(Maybe.None()) to work correctly
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    propagate_enum_type_to_dotcall(validator, default_arg, resolved_t_type)

    # Propagate expected type to DotCall nodes for generic structs (before validation)
    # This allows result.realise(HashMap.new()) to work correctly
    if isinstance(resolved_t_type, StructType):
        propagate_struct_type_to_dotcall(validator, default_arg, resolved_t_type)

    # First validate the argument expression
    validator.validate_expression(default_arg)

    # Then check type compatibility
    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
               expected=str(t_type), got=str(arg_type))


def is_result_enum(t: Any) -> bool:
    """Whether ``t`` is a concrete ``Result<T, E>`` enum.

    Keyed on the interned NAME, not on ``generic_base``: an on-demand intern and a
    monomorphized instance both spell the name the same way, and the ~15 sites that
    already ask this question spell it this way too.
    """
    return isinstance(t, EnumType) and t.name.startswith("Result<")


def result_ok_err(result_enum: EnumType) -> tuple[Type, Type]:
    """The ``(ok, err)`` payload types of a concrete ``Result<T, E>`` enum."""
    ok_variant = result_enum.get_variant("Ok")
    err_variant = result_enum.get_variant("Err")
    if ok_variant is None or err_variant is None:
        raise_internal_error("CE0089", enum=result_enum.name)
    if len(ok_variant.associated_types) != 1 or len(err_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))
    return ok_variant.associated_types[0], err_variant.associated_types[0]


def _result_type_to_str(t: Type) -> str:
    """Format a type for Result<T, E> naming (builtins lowercased)."""
    from sushi_lang.semantics.typesys import BuiltinType
    if isinstance(t, BuiltinType):
        return str(t).lower()
    return str(t)


def ensure_result_type_in_table(
    enum_table: Any,
    ok_type: Type,
    err_type: Type,
    struct_table: Optional[dict] = None,
) -> Optional[EnumType]:
    """Ensure ``Result<ok_type, err_type>`` exists in ``enum_table``, creating it if needed.

    Works with just an enum table so both semantic analysis and codegen can call it.

    THE INVARIANT. ``str(UnknownType("StdError"))`` and ``str(EnumType(name="StdError"))`` are
    both ``"StdError"``, so a Result carrying an *unresolved* payload mangles to the same name
    as the same Result carrying the resolved one. ``EnumType`` hashes on the name alone but
    compares on the variants, so a table poisoned with an unresolved payload would hash-match
    and compare unequal -- a silent cache miss and a duplicate monomorphization, never a crash.
    That is the failure mode that hides.

    So the payloads are resolved HERE, before the name is built, rather than at the call sites:
    it must be impossible to intern a Result without resolving it first. The guard below then
    catches any entry that got in some other way.
    """
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    from sushi_lang.semantics.type_predicates import is_abstract_type

    enums = enum_table.by_name
    structs = struct_table if struct_table is not None else {}
    ok_type = resolve_unknown_type(ok_type, structs, enums)
    err_type = resolve_unknown_type(err_type, structs, enums)

    result_enum_name = f"Result<{_result_type_to_str(ok_type)}, {_result_type_to_str(err_type)}>"

    ok_variant = EnumVariantInfo(name="Ok", associated_types=(ok_type,))
    err_variant = EnumVariantInfo(name="Err", associated_types=(err_type,))
    variants = (ok_variant, err_variant)

    # An abstract Result -- `Result<Either<U, T>, StdError>` from inside a generic body, whose
    # payloads still name the enclosing template's own type params -- is not a real type. Hand
    # the caller the enum it asked for, but keep it OUT of the table: interning it would strand
    # the enum topological sort on an `Either<U, T>` that is never itself interned, which is
    # then misreported as a recursive enum (CE2052). The concrete instantiations are interned
    # separately, at the call sites that bind the parameters.
    if (is_abstract_type(ok_type, structs, enums)
            or is_abstract_type(err_type, structs, enums)):
        return EnumType(
            name=result_enum_name,
            variants=variants,
            generic_base="Result",
            generic_args=(ok_type, err_type),
        )

    existing = enums.get(result_enum_name)
    if existing is not None:
        # Same name, different payloads: one of the two was interned unresolved. Fail loudly
        # at the moment of corruption rather than let it decay into a cache miss.
        if existing.variants and existing.variants != variants:
            raise_internal_error(
                "CE0126",
                name=result_enum_name,
                existing=str([str(t) for v in existing.variants for t in v.associated_types]),
                rebuilt=str([str(t) for v in variants for t in v.associated_types]),
            )
        return existing

    result_enum = EnumType(
        name=result_enum_name,
        variants=variants,
        generic_base="Result",
        generic_args=(ok_type, err_type),
    )

    enums[result_enum_name] = result_enum
    enum_table.order.append(result_enum_name)

    # Register the auto-derived hash() for the on-demand type (mirrors Pass 1.8).
    can_hash, _ = can_enum_be_hashed(result_enum)
    if can_hash:
        register_enum_hash_method(result_enum)

    return result_enum
