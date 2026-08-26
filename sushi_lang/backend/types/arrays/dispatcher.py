"""Array method dispatcher."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, Type, deref_type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

from .methods import core, iterators, hashing
from .addressing import as_array_address
from .fixed_addressing import as_fixed_array_address


def is_builtin_array_method(method_name: str) -> bool:
    """Check if a method name is a built-in array method."""
    # Fixed array methods: len, get, iter, hash, fill, reverse
    # Dynamic array methods: len, get, push, pop, capacity, destroy, free, iter, clone, hash, fill, reverse
    # u8[] specific methods: to_string
    return method_name in {
        "len", "get", "push", "pop", "capacity", "destroy", "free",
        "iter", "to_string", "to_string_checked", "clone", "hash", "fill", "reverse"
    }


def _as_fixed_ir_type(receiver_type: ir.Type) -> ir.ArrayType | None:
    """The `[N x T]` behind a receiver, whether it arrived as a value or as an address."""
    if isinstance(receiver_type, ir.ArrayType):
        return receiver_type
    if isinstance(receiver_type, ir.PointerType) and isinstance(receiver_type.pointee, ir.ArrayType):
        return receiver_type.pointee
    return None


def emit_array_method(
    codegen: 'LLVMCodegen',
    expr: MethodCall,
    receiver_value: ir.Value,
    receiver_type: ir.Type,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for built-in array method calls."""
    method_name = expr.method

    fixed_ir_type = _as_fixed_ir_type(receiver_type)
    if fixed_ir_type is not None:
        # The element type drives the per-slot deep copy of an owning element, and it is the
        # iterator's item type. Dereferenced here rather than per arm, the way the dynamic
        # path below unwraps once for all of its arms.
        fixed_semantic_type = deref_type(semantic_type)
        if not isinstance(fixed_semantic_type, ArrayType):
            raise_internal_error("CE0042", type=type(fixed_semantic_type).__name__)

        def address(*, writable: bool) -> ir.Value:
            """This receiver as an address. ONE rule, where the arms had nine (#480)."""
            return as_fixed_array_address(codegen, expr.receiver, receiver_value,
                                          fixed_ir_type, semantic_type, writable=writable)

        match method_name:
            case "len":
                len_value = ir.Constant(codegen.types.i32, fixed_ir_type.count)
                return codegen.utils.as_i1(len_value) if to_i1 else len_value

            case "get":
                from .methods.safe_access import emit_fixed_array_get_maybe
                index_value = codegen.expressions.emit_expr(expr.args[0])
                if index_value.type != codegen.types.i32:
                    is_signed = index_value.type in (codegen.types.i8, codegen.types.i16, codegen.types.i64)
                    index_value = codegen.utils.convert_int_to_i32(index_value, is_signed=is_signed)
                return emit_fixed_array_get_maybe(codegen, address(writable=False), fixed_ir_type,
                                                  index_value, fixed_semantic_type, to_i1)

            case "iter":
                return iterators.emit_fixed_array_iter(codegen, expr, address(writable=False),
                                                       fixed_ir_type,
                                                       fixed_semantic_type.base_type, to_i1)

            case "hash":
                return hashing.emit_fixed_array_hash_direct(codegen, expr, address(writable=False),
                                                            fixed_ir_type, fixed_semantic_type,
                                                            to_i1)

            case "clone":
                # A fixed array is a value, so the clone is value-in / value-out. It routes
                # through the SAME emitter the struct-field and `let` sinks use, which is
                # what makes it the exact structural inverse of the destructor -- the
                # property a hand-written element loop here would be free to break.
                from sushi_lang.backend.expressions.memory import emit_value_clone
                array_value = codegen.builder.load(address(writable=False), name="clone_src")
                return emit_value_clone(codegen, array_value, fixed_semantic_type)

            case "fill":
                # A borrow, so an owning temporary needs an owner (#475).
                from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
                fill_value = emit_borrowed_arg(codegen, expr.args[0],
                                               fixed_semantic_type.base_type)
                return core.emit_fixed_array_fill(codegen, address(writable=True), fixed_ir_type,
                                                  fill_value, fixed_semantic_type.base_type)

            case "reverse":
                return core.emit_fixed_array_reverse(codegen, address(writable=True), fixed_ir_type)

            case _:
                raise NotImplementedError(f"Fixed array method not implemented: {method_name}")

    if isinstance(receiver_type, ir.PointerType):
        array_struct_type = receiver_type.pointee
    else:
        array_struct_type = receiver_type

    # Every dynamic-array method below reads its receiver through a GEP, so THIS is the one
    # place that turns a descriptor value into an address. A receiver that already IS an
    # address keeps it, which is what makes a mutating method reach the owner: a Name gives
    # its slot and a field read gives a GEP into the struct.
    receiver_value = as_array_address(codegen, receiver_value, array_struct_type,
                                      expr.receiver, semantic_type)

    # The methods on `&T` are the methods on `T`. The arms below read
    # `semantic_type.base_type`, so a reference receiver raised CE0042 and made `a.clone()`
    # unusable as CE2411's escape (#301). Unwrapped ONCE here, not per arm.
    semantic_type = deref_type(semantic_type)

    match method_name:
        case "len":
            return core.emit_dynamic_array_len(codegen, receiver_value, to_i1)

        case "capacity":
            return core.emit_dynamic_array_capacity(codegen, receiver_value, to_i1)

        case "get":
            from .methods.safe_access import emit_dynamic_array_get_maybe
            index_value = codegen.expressions.emit_expr(expr.args[0])
            if index_value.type != codegen.types.i32:
                is_signed = index_value.type in (codegen.types.i8, codegen.types.i16, codegen.types.i64)
                index_value = codegen.utils.convert_int_to_i32(index_value, is_signed=is_signed)
            return emit_dynamic_array_get_maybe(codegen, receiver_value, array_struct_type, index_value, semantic_type, to_i1)

        case "push":
            # The array stores the element shallowly and frees it, so this is a consuming
            # use like List.push and HashMap.insert. The reference unwrap this arm used to
            # do for itself now happens once, above.
            from sushi_lang.backend.ownership import ConsumingUse, consume
            element_value = consume(
                codegen, expr.args[0], codegen.expressions.emit_expr(expr.args[0]),
                getattr(semantic_type, "base_type", None),
                ConsumingUse.CONTAINER_INSERT,
            )
            return core.emit_dynamic_array_push(codegen, receiver_value, array_struct_type, element_value)

        case "pop":
            if not isinstance(semantic_type, DynamicArrayType):
                raise_internal_error("CE0042", type=type(semantic_type).__name__)
            return core.emit_dynamic_array_pop(codegen, receiver_value, array_struct_type,
                                               semantic_type.base_type, to_i1)

        case "free":
            if isinstance(semantic_type, DynamicArrayType):
                element_semantic_type = semantic_type.base_type
            else:
                raise_internal_error("CE0042", type=type(semantic_type).__name__)
            return core.emit_dynamic_array_free(codegen, receiver_value, array_struct_type, element_semantic_type)

        case "destroy":
            return core.emit_dynamic_array_destroy(codegen, receiver_value, array_struct_type, semantic_type)

        case "iter":
            return iterators.emit_dynamic_array_iter(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "clone":
            from .methods.transforms import emit_dynamic_array_clone
            # The element type drives the per-element deep copy of owning elements (#158).
            if not isinstance(semantic_type, DynamicArrayType):
                raise_internal_error("CE0042", type=type(semantic_type).__name__)
            return emit_dynamic_array_clone(codegen, expr, receiver_value, array_struct_type,
                                            to_i1, semantic_type.base_type)

        case "to_string":
            from .methods.transforms import emit_byte_array_to_string
            return emit_byte_array_to_string(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "to_string_checked":
            from .methods.transforms import emit_byte_array_to_string_checked
            return emit_byte_array_to_string_checked(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "hash":
            return hashing.emit_dynamic_array_hash_direct(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "fill":
            # The element type drives the per-slot deep copy of an owning element (#476).
            if not isinstance(semantic_type, DynamicArrayType):
                raise_internal_error("CE0042", type=type(semantic_type).__name__)
            # A borrow, so the temporary behind `arr.fill(s.s(2, 5))` needs an owner (#475).
            from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
            fill_value = emit_borrowed_arg(codegen, expr.args[0], semantic_type.base_type)
            return core.emit_dynamic_array_fill(codegen, receiver_value, array_struct_type,
                                                fill_value, semantic_type.base_type)

        case "reverse":
            return core.emit_dynamic_array_reverse(codegen, receiver_value, array_struct_type)

        case _:
            raise NotImplementedError(f"Dynamic array method not implemented: {method_name}")

