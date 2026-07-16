"""
LLVM emission for the auto-derived enum hash() method.

Hash is computed using FNV-1a by combining the variant tag with the hashes of
its associated values:
    hash = FNV_OFFSET_BASIS
    hash = (hash XOR tag) * FNV_PRIME
    for each associated value in variant:
        hash = (hash XOR value.hash()) * FNV_PRIME

Whether an enum *may* be hashed, and the registration of the method itself, are
semantic concerns and live in semantics/generics/hashing.py. This module only
supplies the emitter, which it deposits in the shared factory registry at import
time.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type, ArrayType, DynamicArrayType, BuiltinType, StructType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend import enum_utils



def _emit_enum_hash(enum_type: Type) -> Any:
    """Create a hash() emitter function for enum types.

    This generates code that combines the tag with variant data hashes using FNV-1a.

    Args:
        enum_type: The enum type (must be EnumType)

    Returns:
        An emitter function that computes the enum hash
    """
    if not isinstance(enum_type, EnumType):
        raise_internal_error("CE0032", type=type(enum_type).__name__)

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for enum.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)
        i32 = codegen.types.i32

        # Enum structure: {i32 tag, [N x i8] data}
        # receiver_value might be a pointer or a value, check its type
        if isinstance(receiver_value.type, ir.PointerType):
            # If it's a pointer, load it first to get the enum value
            enum_value = builder.load(receiver_value, name="enum_val")
        else:
            # Already a value
            enum_value = receiver_value

        # Extract the tag (discriminant) from the enum
        tag = enum_utils.extract_enum_tag(codegen, enum_value, name="enum_tag")
        tag_u64 = builder.zext(tag, u64)

        # Initialize hash with FNV offset basis XOR tag
        hash_value = emit_fnv1a_init(codegen)
        hash_value = emit_fnv1a_combine(codegen, hash_value, tag_u64)

        # If all variants have no associated data, just return tag-based hash
        has_any_data = any(len(v.associated_types) > 0 for v in enum_type.variants)
        if not has_any_data:
            return hash_value

        # Create blocks for each variant that has associated data
        merge_block = builder.append_basic_block(name="hash_merge")
        hash_phi_incoming = []

        # Remember the block where we create the switch (for unit variant default case)
        switch_block = builder.block

        # Create switch instruction (default goes to merge_block for unit variants)
        switch = builder.switch(tag, merge_block)

        # For each variant, create a case that hashes the associated data
        for variant_idx, variant in enumerate(enum_type.variants):
            if len(variant.associated_types) == 0:
                # Unit variant - no data to hash, will use default branch to merge_block
                continue

            # Create block for this variant
            variant_block = builder.append_basic_block(name=f"hash_variant_{variant.name}")
            switch.add_case(ir.Constant(i32, variant_idx), variant_block)
            builder.position_at_end(variant_block)

            # Hash the variant's associated data
            variant_hash = _emit_variant_data_hash(codegen, enum_value, variant, hash_value)

            # Branch to merge block from the CURRENT block (which may have changed during hash emission)
            current_block = builder.block
            hash_phi_incoming.append((variant_hash, current_block))
            builder.branch(merge_block)

        # Merge block - collect hash values from all branches
        builder.position_at_end(merge_block)

        # If no variants had data, just return the initial hash
        if not hash_phi_incoming:
            return hash_value

        # Create phi node to select the correct hash value
        hash_phi = builder.phi(u64, name="final_hash")

        # Add incoming values from variant blocks (with associated data)
        for hash_val, block in hash_phi_incoming:
            hash_phi.add_incoming(hash_val, block)

        # Add incoming value from switch default (for unit variants)
        # Unit variants fall through to merge_block with the initial hash_value
        hash_phi.add_incoming(hash_value, switch_block)

        return hash_phi

    return emitter


def _emit_variant_data_hash(codegen: Any, enum_value: ir.Value, variant: Any, initial_hash: ir.Value) -> ir.Value:
    """Emit code to hash the associated data for a specific enum variant.

    This unpacks the variant data from the enum's data field and hashes each value.

    Args:
        codegen: The LLVM code generator
        enum_value: LLVM value of the enum
        variant: EnumVariantInfo with associated_types
        initial_hash: Starting hash value (already includes tag)

    Returns:
        Combined hash value as u64
    """

    builder = require_builder(codegen)
    builder = codegen.builder

    # Extract the data field from enum: [N x i8] array
    data_array = enum_utils.extract_enum_data(codegen, enum_value, name="enum_data")

    # Allocate temporary storage for the data array
    data_array_type = enum_value.type.elements[1]  # [N x i8]
    temp_alloca = builder.alloca(data_array_type, name="data_temp")
    builder.store(data_array, temp_alloca)

    # Cast to i8* for byte-level access
    data_ptr = builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Unpack and hash each associated value
    hash_value = initial_hash
    offset = 0

    for assoc_idx, assoc_type in enumerate(variant.associated_types):
        # Get LLVM type for this associated value
        llvm_type = codegen.types.ll_type(assoc_type)

        # Calculate pointer to this value in the data array
        from sushi_lang.backend.expressions import memory
        value_size = memory.get_type_size(llvm_type)

        # Get pointer at current offset
        value_ptr_i8 = builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)], name=f"assoc{assoc_idx}_ptr")
        value_ptr_typed = builder.bitcast(value_ptr_i8, ir.PointerType(llvm_type), name=f"assoc{assoc_idx}_ptr_typed")

        # Load the value
        value = builder.load(value_ptr_typed, name=f"assoc{assoc_idx}_value", align=1)  # under-aligned enum payload (#145)

        # Get hash of this value by calling its .hash() method
        value_hash = _emit_associated_value_hash(codegen, value, assoc_type)

        # Combine using FNV-1a
        hash_value = emit_fnv1a_combine(codegen, hash_value, value_hash)

        # Move to next value
        offset += value_size

    return hash_value


def _emit_associated_value_hash(codegen: Any, value: ir.Value, value_type: Type) -> ir.Value:
    """Emit code to get the hash of an associated value.

    This recursively calls the appropriate .hash() method based on the value type.

    Args:
        codegen: The LLVM code generator
        value: LLVM value to hash
        value_type: Semantic type of the value

    Returns:
        Hash value as u64
    """
    from sushi_lang.semantics.ast import MethodCall, Name

    require_builder(codegen)
    # For primitive types, call their hash() method inline
    if isinstance(value_type, BuiltinType):
        # Ensure hash methods are registered
        import sushi_lang.backend.types.primitives.hashing  # noqa: F401
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the builtin hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    # For struct types, call their hash() method
    elif isinstance(value_type, StructType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the struct hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    # For enum types, call their hash() method recursively
    elif isinstance(value_type, EnumType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the enum hash emitter recursively
        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    # For array types (fixed and dynamic), call their hash() method
    elif isinstance(value_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the array hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    else:
        raise_internal_error("CE0052", type=str(value_type))




# Supply the enum hash() emitter to semantics/generics/hashing.py, which owns
# hashability analysis and the registration itself.
register_hash_emitter_factory("enum", _emit_enum_hash)
