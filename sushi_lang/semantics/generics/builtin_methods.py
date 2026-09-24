"""One answer to "does the compiler already define this method on this type?"."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    FunctionType,
    ReferenceType,
    StructType,
    Type,
)


def builtin_method_exists(receiver_type: Type | None, method_name: str,
                          derived_methods: DerivedMethodTable) -> bool:
    """Is `method_name` a compiler-defined method on `receiver_type`?

    `derived_methods` is the COMPILATION's auto-derived pair (hash, clone) -- one program's
    `Point` is not another's, however alike the two names look (#601).
    """
    if receiver_type is None:
        return False

    if isinstance(receiver_type, ReferenceType):
        receiver_type = receiver_type.referenced_type

    if isinstance(receiver_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method
        return is_builtin_array_method(method_name)

    if receiver_type == BuiltinType.STRING:
        from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
        from sushi_lang.semantics.generics.primitives import has_primitive_method
        return (is_builtin_string_method(method_name)
                or has_primitive_method(receiver_type, method_name))

    if isinstance(receiver_type, BuiltinType):
        from sushi_lang.semantics.generics.primitives import has_primitive_method
        return has_primitive_method(receiver_type, method_name)

    # A function value carries clone(): a closure read out of a field or a container is a
    # borrow, so consuming it is CE2411 and the explicit copy is the escape.
    if isinstance(receiver_type, FunctionType):
        from sushi_lang.semantics.generics.closures import is_builtin_function_method
        return is_builtin_function_method(method_name)

    # The generic base names the family of a built-in container (#805).
    if isinstance(receiver_type, EnumType):
        if is_instance_of(receiver_type, "Result"):
            from sushi_lang.semantics.generics.results import is_builtin_result_method
            if is_builtin_result_method(method_name):
                return True
        elif is_instance_of(receiver_type, "Maybe"):
            from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
            if is_builtin_maybe_method(method_name):
                return True
        return derived_methods.get_method(receiver_type, method_name) is not None

    if isinstance(receiver_type, StructType):
        if is_instance_of(receiver_type, "Own"):
            from sushi_lang.semantics.generics.own import is_builtin_own_method
            if is_builtin_own_method(method_name):
                return True
        elif is_instance_of(receiver_type, "HashMap"):
            from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method
            if is_builtin_hashmap_method(method_name):
                return True
        elif is_instance_of(receiver_type, "List"):
            from sushi_lang.semantics.generics.list import is_builtin_list_method
            if is_builtin_list_method(method_name):
                return True
        # A container still carries the auto-derived hash (the derive pass's registration has no
        # container exclusion), and codegen's auto-derived step precedes the extension
        # fallback -- so an extension of that name would be dead there too.
        return derived_methods.get_method(receiver_type, method_name) is not None

    return False


def reject_builtin_miscount(reporter: Any, call: Any, callee: str,
                            arity: Mapping[str, int]) -> bool:
    """CE2009: a built-in method or static took the wrong number of arguments.

    `arity` is the family's table (`MethodFamily.arity`); a name it does not hold is
    measured elsewhere. `callee` is the name the text shows. Answers whether it reported.
    """
    expected = arity.get(call.method)
    if expected is None or len(call.args) == expected:
        return False
    er.emit(reporter, er.ERR.CE2009, call.loc,
            name=callee, expected=expected, got=len(call.args))
    return True


def derived_method(target_type: Type, *, name: str, kind: str, return_type: Type,
                   factory_getter: Callable[[str], Any], ice: Any) -> Any:
    """The auto-derived zero-argument method (`hash`, `clone`) of a type, to register.

    The emitter is LAZY: the backend deposits its factory for `kind` at import time, and
    semantics may not import the backend, so the factory is found at the first emission.
    A missing factory is the internal error `ice`. The count is the derived family's row
    (`MethodFamily.arity`), so the method carries no validator.
    """
    def emit(codegen, call, receiver_value, receiver_type, to_i1):
        factory = factory_getter(kind)
        if factory is None:
            raise_internal_error(ice.code, kind=kind)
        return factory(target_type)(codegen, call, receiver_value, receiver_type, to_i1)

    from sushi_lang.sushi_stdlib.src.common import BuiltinMethod
    return BuiltinMethod(
        name=name,
        parameter_types=[],
        return_type=return_type,
        description=f"Auto-derived {name} for {kind} {target_type}",
        semantic_validator=None,
        llvm_emitter=emit,
    )
