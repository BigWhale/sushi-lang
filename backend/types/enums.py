"""
Extension methods for enum types.

Implemented methods:
- hash() -> u64: Auto-derived hash function for enums with hashable variant data

Hash is computed using FNV-1a algorithm by combining tag with variant data hashes:
    hash = FNV_OFFSET_BASIS
    hash = (hash XOR tag) * FNV_PRIME
    for each associated value in variant:
        hash = (hash XOR value.hash()) * FNV_PRIME

Known limitations:
- Generic enums cannot be hashed (must be monomorphized first)
- Nested enums work if all variant types are hashable
- Arrays in variant data are supported (both fixed and dynamic arrays)
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import EnumType, Type, ArrayType, DynamicArrayType, BuiltinType, StructType
from semantics.generics.types import GenericEnumType
import llvmlite.ir as ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from internals import errors as er
from internals.errors import raise_internal_error
from backend.utils import require_builder
from stdlib.src.common import register_builtin_method, BuiltinMethod
from backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from backend import enum_utils


def _validate_enum_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on enum types.

    Checks:
    - No arguments to hash()
    - Enum doesn't contain array-typed variant data
    - Enum is not generic
    """
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{target_type}.hash", expected=0, got=len(call.args))

    # Validation is done in can_enum_be_hashed() - no need to check here
    # Arrays are now supported if they have hashable element types
    pass


def can_enum_be_hashed(enum_type: EnumType, visited: set = None, path: list = None) -> tuple[bool, str]:
    """Check if an enum type can have an auto-derived hash method.

    An enum can be hashed if:
    - It's not a generic enum (GenericEnumType)
    - All variant associated types are hashable
    - It has no UnknownType fields (types not yet resolved)
    - All array fields have hashable element types (recursive check)
    - All nested enum/struct fields can also be hashed (recursive check)

    Args:
        enum_type: The enum type to check
        visited: Set of enum names already visited (for cycle detection)
        path: List of enum names in current path (for error messages)

    Returns:
        Tuple of (can_hash, reason) where reason explains why if False
    """
    from semantics.typesys import UnknownType

    # Initialize tracking for recursive calls
    if visited is None:
        visited = set()
    if path is None:
        path = []

    # Detect cycles (shouldn't happen with Sushi's type system, but be defensive)
    if enum_type.name in visited:
        return False, f"recursive enum type: {' -> '.join(path + [enum_type.name])}"

    visited.add(enum_type.name)
    path.append(enum_type.name)

    # Generic enums cannot be hashed (should be monomorphized first)
    if isinstance(enum_type, GenericEnumType):
        return False, f"generic enum {enum_type.name} (should be monomorphized first)"

    # Check each variant's associated types
    for variant in enum_type.variants:
        for assoc_idx, assoc_type in enumerate(variant.associated_types):
            # Skip enums with unresolved types (will be registered later)
            if isinstance(assoc_type, UnknownType):
                return False, f"variant {variant.name} has unresolved type '{assoc_type.name}'"

            # Arrays must have hashable element types (recursive check)
            if isinstance(assoc_type, (ArrayType, DynamicArrayType)):
                from backend.types.arrays.methods.hashing import can_array_be_hashed
                can_hash, reason = can_array_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            # Nested enums must also be hashable (recursive check)
            if isinstance(assoc_type, EnumType):
                can_hash, reason = can_enum_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            # Nested structs must also be hashable (recursive check)
            if isinstance(assoc_type, StructType):
                from backend.types.structs import can_struct_be_hashed
                can_hash, reason = can_struct_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

    return True, "all variant types are hashable"


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
    from semantics.ast import MethodCall, Name

    builder = require_builder(codegen)
    builder = codegen.builder
    u64 = ir.IntType(INT64_BIT_WIDTH)

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
        from backend.expressions import memory
        value_size = memory.get_type_size(llvm_type)

        # Get pointer at current offset
        value_ptr_i8 = builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)], name=f"assoc{assoc_idx}_ptr")
        value_ptr_typed = builder.bitcast(value_ptr_i8, ir.PointerType(llvm_type), name=f"assoc{assoc_idx}_ptr_typed")

        # Load the value
        value = builder.load(value_ptr_typed, name=f"assoc{assoc_idx}_value")

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
    from semantics.ast import MethodCall, Name

    builder = require_builder(codegen)
    # For primitive types, call their hash() method inline
    if isinstance(value_type, BuiltinType):
        # Ensure hash methods are registered
        import backend.types.primitives.hashing  # noqa: F401
        from stdlib.src.common import get_builtin_method

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
        from stdlib.src.common import get_builtin_method

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
        from stdlib.src.common import get_builtin_method

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
        from stdlib.src.common import get_builtin_method

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


def register_enum_hash_method(enum_type: EnumType) -> None:
    """Register the auto-derived hash() method for an enum type.

    This should be called during semantic analysis for each enum that
    can be hashed (i.e., has no array-typed variant data and is not generic).

    After monomorphization, this should only be called from Pass 1.8 (hash_registration.py).
    If duplicate registration is detected, it indicates a bug in the compiler.

    Args:
        enum_type: The enum type to register hash() for
    """
    can_hash, reason = can_enum_be_hashed(enum_type)
    if not can_hash:
        return  # Don't register hash for unsupported enums

    # Check if hash is already registered (shouldn't happen after monomorphization)
    from stdlib.src.common import get_builtin_method
    existing_hash = get_builtin_method(enum_type, "hash")
    if existing_hash is not None:
        # This is a compiler bug - hash should only be registered once in Pass 1.8
        print(f"WARNING: hash() already registered for {enum_type.name} (duplicate registration)")
        return  # Skip duplicate registration

    register_builtin_method(
        enum_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=f"Auto-derived hash for enum {enum_type}",
            semantic_validator=_validate_enum_hash,
            llvm_emitter=_emit_enum_hash(enum_type),
        )
    )
