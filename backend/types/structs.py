"""
Extension methods for struct types.

Implemented methods:
- hash() -> u64: Auto-derived hash function for structs with primitive fields

Hash is computed using FNV-1a algorithm by combining field hashes:
    hash = FNV_OFFSET_BASIS
    for each field:
        hash = (hash XOR field.hash()) * FNV_PRIME

Known limitations:
- Structs with array fields (fixed or dynamic) cannot be hashed
- Generic structs cannot be hashed
- Nested structs work if all nested fields are hashable
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import StructType, Type, ArrayType, DynamicArrayType, BuiltinType, EnumType
from semantics.generics.types import GenericStructType
import llvmlite.ir as ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from internals import errors as er
from internals.errors import raise_internal_error
from stdlib.src.common import register_builtin_method, BuiltinMethod
from backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _validate_struct_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on struct types.

    Checks:
    - No arguments to hash()
    - Struct doesn't contain array fields
    - Struct is not generic
    """
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{target_type}.hash", expected=0, got=len(call.args))

    # Validation is done in can_struct_be_hashed() - no need to check here
    # Arrays are now supported if they have hashable element types
    pass


def can_struct_be_hashed(struct_type: StructType, visited: set = None, path: list = None) -> tuple[bool, str]:
    """Check if a struct type can have an auto-derived hash method.

    A struct can be hashed if:
    - It's not a generic struct (GenericStructType)
    - It has no UnknownType fields (types not yet resolved)
    - All array fields have hashable element types (recursive check)
    - All enum fields are hashable (recursive check)
    - All nested struct fields can also be hashed (recursive check)

    Args:
        struct_type: The struct type to check
        visited: Set of struct names already visited (for cycle detection)
        path: List of struct names in current path (for error messages)

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
    if struct_type.name in visited:
        return False, f"recursive struct type: {' -> '.join(path + [struct_type.name])}"

    visited.add(struct_type.name)
    path.append(struct_type.name)

    # Generic structs cannot be hashed (should be monomorphized first)
    if isinstance(struct_type, GenericStructType):
        return False, f"generic struct {struct_type.name} (should be monomorphized first)"

    # Check each field (fields is a tuple of (name, type) tuples)
    for field_name, field_type in struct_type.fields:
        # Skip structs with unresolved types (will be registered later)
        if isinstance(field_type, UnknownType):
            return False, f"field '{field_name}' has unresolved type '{field_type.name}'"

        # Arrays must have hashable element types (recursive check)
        if isinstance(field_type, (ArrayType, DynamicArrayType)):
            from backend.types.arrays.methods.hashing import can_array_be_hashed
            can_hash, reason = can_array_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        # Enums must also be hashable (recursive check)
        if isinstance(field_type, EnumType):
            from backend.types.enums import can_enum_be_hashed
            can_hash, reason = can_enum_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        # Nested structs must also be hashable (recursive check)
        if isinstance(field_type, StructType):
            can_hash, reason = can_struct_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

    return True, "all fields are hashable"


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

        if codegen.builder is None:
            raise_internal_error("CE0009")
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
    from semantics.ast import MethodCall, Name
    from semantics.typesys import BuiltinType

    if codegen.builder is None:
        raise_internal_error("CE0009")
    builder = codegen.builder

    # For primitive types, call their hash() method inline
    if isinstance(field_type, BuiltinType):
        # Ensure hash methods are registered
        import backend.types.primitives.hashing  # noqa: F401
        from stdlib.src.common import get_builtin_method

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

    # For enum types (like Maybe<i32>, Result<T>), call their hash() method
    elif isinstance(field_type, EnumType):
        from stdlib.src.common import get_builtin_method

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
        from stdlib.src.common import get_builtin_method

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


def register_struct_hash_method(struct_type: StructType) -> None:
    """Register the auto-derived hash() method for a struct type.

    This should be called during semantic analysis for each struct that
    can be hashed (i.e., has no array fields and is not generic).

    After Phase 3, this should only be called from Pass 1.8 (hash_registration.py).
    If duplicate registration is detected, it indicates a bug in the compiler.

    Args:
        struct_type: The struct type to register hash() for
    """
    can_hash, reason = can_struct_be_hashed(struct_type)
    if not can_hash:
        return  # Don't register hash for unsupported structs

    # Check if hash is already registered (shouldn't happen after Phase 3)
    from stdlib.src.common import get_builtin_method
    existing_hash = get_builtin_method(struct_type, "hash")
    if existing_hash is not None:
        # This is a compiler bug - hash should only be registered once in Pass 1.8
        print(f"WARNING: hash() already registered for {struct_type.name} (duplicate registration)")
        return  # Skip duplicate registration

    register_builtin_method(
        struct_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=f"Auto-derived hash for struct {struct_type}",
            semantic_validator=_validate_struct_hash,
            llvm_emitter=_emit_struct_hash(struct_type),
        )
    )
