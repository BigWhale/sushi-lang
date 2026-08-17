# semantics/generics/builtin_methods.py
"""One answer to "does the compiler already define this method on this type?"."""
from __future__ import annotations

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

# Named StructTypes/EnumTypes whose methods come from their own generic machinery rather
# than from the struct/enum auto-derivation. Each maps to the predicate that owns it.
_STDIO_RECEIVERS = (BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR)


def _struct_enum_derived(receiver_type: Type, method_name: str) -> bool:
    """The Pass 1.8 auto-derived pair (hash, clone), read from the registry."""
    from sushi_lang.sushi_stdlib.src.common import get_builtin_method
    return get_builtin_method(receiver_type, method_name) is not None


def builtin_method_exists(receiver_type: Type | None, method_name: str) -> bool:
    """Is `method_name` a compiler-defined method on `receiver_type`?"""
    if receiver_type is None:
        return False

    # Methods on &T are the methods on T.
    if isinstance(receiver_type, ReferenceType):
        receiver_type = receiver_type.referenced_type

    if isinstance(receiver_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method
        return is_builtin_array_method(method_name)

    if receiver_type == BuiltinType.STRING:
        # string carries BOTH families: the stdlib string methods and the primitive
        # to_str/hash. Checking only the first is what left string.to_str() un-inferred.
        from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
        from sushi_lang.semantics.generics.primitives import has_primitive_method
        return (is_builtin_string_method(method_name)
                or has_primitive_method(receiver_type, method_name))

    if receiver_type in _STDIO_RECEIVERS:
        from sushi_lang.sushi_stdlib.src.io.stdio import is_builtin_stdio_method
        return is_builtin_stdio_method(method_name)

    if receiver_type == BuiltinType.FILE:
        from sushi_lang.sushi_stdlib.src.io.files import is_builtin_file_method
        return is_builtin_file_method(method_name)

    if isinstance(receiver_type, BuiltinType):
        from sushi_lang.semantics.generics.primitives import has_primitive_method
        return has_primitive_method(receiver_type, method_name)

    # A function value carries clone(): a closure read out of a field or a container is a
    # borrow, so consuming it is CE2411 and the explicit copy is the escape.
    if isinstance(receiver_type, FunctionType):
        from sushi_lang.semantics.generics.closures import is_builtin_function_method
        return is_builtin_function_method(method_name)

    # The interned name is the authority for the generic containers -- angle brackets are
    # the INTERNAL spelling and must not be "fixed" to @( ) here.
    if isinstance(receiver_type, EnumType):
        if receiver_type.name.startswith("Result<"):
            from sushi_lang.semantics.generics.results import is_builtin_result_method
            if is_builtin_result_method(method_name):
                return True
        elif receiver_type.name.startswith("Maybe<"):
            from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
            if is_builtin_maybe_method(method_name):
                return True
        return _struct_enum_derived(receiver_type, method_name)

    if isinstance(receiver_type, StructType):
        if receiver_type.name.startswith("Own<"):
            from sushi_lang.semantics.generics.own import is_builtin_own_method
            if is_builtin_own_method(method_name):
                return True
        elif receiver_type.name.startswith("HashMap<"):
            from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method
            if is_builtin_hashmap_method(method_name):
                return True
        elif receiver_type.name.startswith("List<"):
            from sushi_lang.semantics.generics.list import is_builtin_list_method
            if is_builtin_list_method(method_name):
                return True
        # A container still carries the auto-derived hash (Pass 1.8's registration has no
        # container exclusion), and codegen's auto-derived step precedes the extension
        # fallback -- so an extension of that name would be dead there too.
        return _struct_enum_derived(receiver_type, method_name)

    return False
