# semantics/generics/builtin_methods.py
"""One answer to "does the compiler already define this method on this type?".

Method resolution asks that question in three places -- Pass 2 validation
(`passes/types/calls/methods.py:validate_method_call`), Pass 2 inference
(`passes/types/method_registry.py`) and codegen
(`backend/expressions/calls/dispatcher.py`) -- and each layer resolves a built-in BEFORE
falling back to a user extension method. So a user extension whose name collides with a
built-in is not merely lower-priority: it is compiled and then never called. CE2097 rejects
it, and this module is what CE2097 keys on.

**This is a seam, not a fallback.** Every predicate below already existed; they were simply
never assembled, so the shadowing check could only see the struct/enum pair and the other
nine families went uncovered (#239). `tests/unit/test_builtin_method_seam.py` pins this
family list against `validate_method_call`'s, because two places answering one question
drift.

**Layering**: every predicate lives in `semantics/` or `sushi_stdlib/`, never `backend/`.
That is load-bearing, not incidental. `sushi_stdlib/src/common.py`'s builtin-method registry
looks like the natural home for this question, but it is populated by the backend at import
time and the pipeline imports codegen lazily, AFTER semantic analysis -- so from a semantics
pass it answers differently depending on what else the process imported. The struct/enum
auto-derived pair is the one entry that IS registered from semantics (Pass 1.8), so it alone
is read from the registry here.

**Perks are deliberately absent.** A perk implementation is the sanctioned way to replace a
built-in -- it wins at all three layers on purpose -- and it is what CE2097's help text
points users at. Perk impls are collected into `PerkImplementationTable`, never
`ExtensionTable`, so no exclusion is needed here; the property is pinned by a test.
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    ReferenceType,
    StructType,
    Type,
)

# Named StructTypes/EnumTypes whose methods come from their own generic machinery rather
# than from the struct/enum auto-derivation. Each maps to the predicate that owns it.
_STDIO_RECEIVERS = (BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR)


def _struct_enum_derived(receiver_type: Type, method_name: str) -> bool:
    """The Pass 1.8 auto-derived pair (hash, clone), read from the registry.

    Safe to read here, unlike the rest of the registry: Pass 1.8 registers these from
    semantics before Pass 2 runs, so the answer does not depend on import order.
    """
    from sushi_lang.sushi_stdlib.src.common import get_builtin_method
    return get_builtin_method(receiver_type, method_name) is not None


def builtin_method_exists(receiver_type: Type | None, method_name: str) -> bool:
    """Is `method_name` a compiler-defined method on `receiver_type`?

    Mirrors the receiver dispatch of `validate_method_call`, family for family and in the
    same order, so "would a built-in win here?" gets one answer.
    """
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
