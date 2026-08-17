"""LLVM emission for the auto-derived struct hash() method."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type, ArrayType, DynamicArrayType, EnumType
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory, register_clone_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _emit_struct_hash(prim_type: Type) -> Any:
    """Create a hash() emitter function for struct types."""
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

        hash_value = emit_fnv1a_init(codegen)

        for field_idx, (field_name, field_type) in enumerate(struct_type.fields):
            if isinstance(receiver_value.type, ir.PointerType):
                struct_value = builder.load(receiver_value, name="struct_val")
            else:
                struct_value = receiver_value

            field_value = builder.extract_value(struct_value, field_idx, name=f"field_{field_name}")

            field_hash = _emit_field_hash(codegen, field_value, field_type)

            hash_value = emit_fnv1a_combine(codegen, hash_value, field_hash)

        return hash_value

    return emitter


def _emit_field_hash(codegen: Any, field_value: ir.Value, field_type: Type) -> ir.Value:
    """Emit code to get the hash of a field value."""
    from sushi_lang.semantics.ast import MethodCall, Name
    from sushi_lang.semantics.typesys import BuiltinType

    builder = require_builder(codegen)
    builder = codegen.builder

    if isinstance(field_type, BuiltinType):
        import sushi_lang.backend.types.primitives.hashing  # noqa: F401
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

        fake_call = MethodCall(
            receiver=Name(id="field", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, field_value, field_value.type, False
        )

    elif isinstance(field_type, StructType):

        nested_hash = emit_fnv1a_init(codegen)

        for nested_idx, (nested_name, nested_type) in enumerate(field_type.fields):
            nested_field = builder.extract_value(field_value, nested_idx, name=f"nested_{nested_name}")

            nested_field_hash = _emit_field_hash(codegen, nested_field, nested_type)

            nested_hash = emit_fnv1a_combine(codegen, nested_hash, nested_field_hash)

        return nested_hash

    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(field_type, GenericTypeRef) and field_type.base_name == "Result":
        if len(field_type.type_args) >= 2:
            from sushi_lang.semantics.generics.results import ensure_result_type_in_table
            ok_type = field_type.type_args[0]
            err_type = field_type.type_args[1]
            result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)
            if result_enum is not None:
                field_type = result_enum

    if isinstance(field_type, EnumType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

        fake_call = MethodCall(
            receiver=Name(id="field", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, field_value, field_value.type, False
        )

    elif isinstance(field_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(field_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(field_type))

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


register_hash_emitter_factory("struct", _emit_struct_hash)


def _emit_struct_clone(target_type: Type) -> Any:
    """Create a clone() emitter for a struct type (#134)."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        from sushi_lang.backend.expressions.memory import emit_value_clone
        value = receiver_value
        if isinstance(value.type, ir.PointerType):
            value = codegen.builder.load(value, name="clone_recv")
        return emit_value_clone(codegen, value, target_type)

    return emitter


register_clone_emitter_factory("struct", _emit_struct_clone)
