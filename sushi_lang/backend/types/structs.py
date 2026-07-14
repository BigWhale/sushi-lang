"""
LLVM emission for the auto-derived struct hash() method.

Hash is computed using FNV-1a by combining field hashes:
    hash = FNV_OFFSET_BASIS
    for each field:
        hash = (hash XOR field.hash()) * FNV_PRIME

Whether a struct *may* be hashed, and the registration of the method itself,
are semantic concerns and live in semantics/generics/hashing.py. This module
only supplies the emitter, which it deposits in the shared factory registry at
import time.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type, ArrayType, DynamicArrayType, EnumType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _emit_struct_hash(prim_type: Type) -> Any:
    """Create a hash() emitter function for struct types.

    This generates code that combines all field hashes using FNV-1a.

    Args:
        prim_type: The struct type (must be StructType)

    Returns:
        An emitter function that computes the struct hash
    """
    if not isinstance(prim_type, StructType):
        raise_internal_error("CE0032", type=type(prim_type).__name__)

    struct_type = prim_type

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for struct.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)

        # Initialize hash with FNV offset basis
        hash_value = emit_fnv1a_init(codegen)

        # Combine hash of each field (fields is a tuple of (name, type) tuples)
        for field_idx, (field_name, field_type) in enumerate(struct_type.fields):
            # Extract field value from struct using extractvalue
            # receiver_value might be a pointer or a value, check its type
            if isinstance(receiver_value.type, ir.PointerType):
                # If it's a pointer, load it first to get the struct value
                struct_value = builder.load(receiver_value, name="struct_val")
            else:
                # Already a value
                struct_value = receiver_value

            # Extract field value using extractvalue (LLVM instruction for struct field access)
            field_value = builder.extract_value(struct_value, field_idx, name=f"field_{field_name}")

            # Get hash of this field by calling its .hash() method
            field_hash = _emit_field_hash(codegen, field_value, field_type)

            # Combine using FNV-1a: hash = (hash XOR field_hash) * FNV_PRIME
            hash_value = emit_fnv1a_combine(codegen, hash_value, field_hash)

        return hash_value

    return emitter


def _emit_field_hash(codegen: Any, field_value: ir.Value, field_type: Type) -> ir.Value:
    """Emit code to get the hash of a field value.

    This recursively calls the appropriate .hash() method based on the field type.

    Args:
        codegen: The LLVM code generator
        field_value: LLVM value of the field
        field_type: Semantic type of the field

    Returns:
        Hash value as u64
    """
    from sushi_lang.semantics.ast import MethodCall, Name
    from sushi_lang.semantics.typesys import BuiltinType

    builder = require_builder(codegen)
    builder = codegen.builder

    # For primitive types, call their hash() method inline
    if isinstance(field_type, BuiltinType):
        # Ensure hash methods are registered
        import sushi_lang.backend.types.primitives.hashing  # noqa: F401
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="field", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the builtin hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, field_value, field_value.type, False
        )

    # For nested structs, recursively emit their hash
    elif isinstance(field_type, StructType):
        # Recursively hash the nested struct fields inline
        # field_value is already a struct value, process it directly
        u64 = ir.IntType(INT64_BIT_WIDTH)

        # Initialize hash with FNV offset basis
        nested_hash = emit_fnv1a_init(codegen)

        # Combine hash of each field in the nested struct
        for nested_idx, (nested_name, nested_type) in enumerate(field_type.fields):
            # Extract nested field value
            nested_field = builder.extract_value(field_value, nested_idx, name=f"nested_{nested_name}")

            # Recursively get hash of this nested field
            nested_field_hash = _emit_field_hash(codegen, nested_field, nested_type)

            # Combine using FNV-1a
            nested_hash = emit_fnv1a_combine(codegen, nested_hash, nested_field_hash)

        return nested_hash

    # For Result<T, E> types (GenericTypeRef or ResultType), convert to EnumType and hash
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import ResultType
    if isinstance(field_type, GenericTypeRef) and field_type.base_name == "Result":
        # Convert GenericTypeRef("Result", [T, E]) to Result enum
        if len(field_type.type_args) >= 2:
            from sushi_lang.semantics.generics.results import ensure_result_type_in_table
            ok_type = field_type.type_args[0]
            err_type = field_type.type_args[1]
            result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)
            if result_enum is not None:
                field_type = result_enum

    # For enum types (like Maybe<i32>, Result<T>), call their hash() method
    if isinstance(field_type, EnumType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="field", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the enum hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, field_value, field_value.type, False
        )

    # For array types (fixed and dynamic), call their hash() method
    elif isinstance(field_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="field", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # IMPORTANT: field_value from extract_value is an array VALUE, not a pointer.
        # The array hash emitters (_emit_fixed_array_hash and _emit_dynamic_array_hash)
        # already handle this case - they check if the value is a pointer or a value,
        # and allocate temporary space if needed (see lines 132-137 in hashing.py).
        # So we can just pass field_value directly!
        return hash_method.llvm_emitter(
            codegen, fake_call, field_value, field_value.type, False
        )

    else:
        raise_internal_error("CE0052", type=str(field_type))




# Supply the struct hash() emitter to semantics/generics/hashing.py, which owns
# hashability analysis and the registration itself.
register_hash_emitter_factory("struct", _emit_struct_hash)
