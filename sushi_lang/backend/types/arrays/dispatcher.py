"""Array method dispatcher."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import DynamicArrayType, Type, deref_type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

from .methods import core, iterators, hashing


def is_builtin_array_method(method_name: str) -> bool:
    """Check if a method name is a built-in array method."""
    # Fixed array methods: len, get, iter, hash, fill, reverse
    # Dynamic array methods: len, get, push, pop, capacity, destroy, free, iter, clone, hash, fill, reverse
    # u8[] specific methods: to_string
    return method_name in {
        "len", "get", "push", "pop", "capacity", "destroy", "free",
        "iter", "to_string", "to_string_checked", "clone", "hash", "fill", "reverse"
    }


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

    if isinstance(receiver_type, ir.ArrayType):
        match method_name:
            case "len":
                array_size = receiver_type.count
                len_value = ir.Constant(codegen.types.i32, array_size)
                return codegen.utils.as_i1(len_value) if to_i1 else len_value

            case "get":
                from .methods.safe_access import emit_fixed_array_get_maybe
                index_arg = expr.args[0]
                index_value = codegen.expressions.emit_expr(index_arg)
                if index_value.type != codegen.types.i32:
                    is_signed = index_value.type in (codegen.types.i8, codegen.types.i16, codegen.types.i64)
                    index_value = codegen.utils.convert_int_to_i32(index_value, is_signed=is_signed)
                return emit_fixed_array_get_maybe(codegen, receiver_value, receiver_type, index_value, semantic_type, to_i1)

            case "iter":
                return iterators.emit_fixed_array_iter(codegen, expr, receiver_value, receiver_type, to_i1)

            case "hash":
                return hashing.emit_fixed_array_hash_direct(codegen, expr, receiver_value, receiver_type, to_i1)

            case "clone":
                # A fixed array is a value, so the clone is value-in / value-out. It routes
                # through the SAME emitter the struct-field and `let` sinks use, which is
                # what makes it the exact structural inverse of the destructor -- the
                # property a hand-written element loop here would be free to break.
                from sushi_lang.backend.expressions.memory import emit_value_clone
                return emit_value_clone(codegen, receiver_value, semantic_type)

            case "fill":
                # Fixed array fill: fill all elements with a value
                # Need to get pointer to the array variable for in-place modification
                #
                # Deliberately NOT routed through resolve_name_slot (#248), unlike every
                # other address site. fill/reverse WRITE through the pointer, and a
                # constant's global carries `global_constant = true` -- i.e. .rodata, so
                # the store would be undefined behaviour (a likely SIGBUS) rather than a
                # diagnostic. Pass 2 rejects a mutating method on a constant receiver
                # with CE2096, so a constant never reaches here; keeping this on the
                # locals-only lookup means it cannot start writing to .rodata even if
                # that check is ever bypassed.
                from sushi_lang.semantics.ast import Name
                if isinstance(expr.receiver, Name):
                    array_ptr = codegen.memory.find_local_slot(expr.receiver.id)
                else:
                    array_ptr = codegen.builder.alloca(receiver_type, name="temp_array")
                    codegen.builder.store(receiver_value, array_ptr)
                fill_value = codegen.expressions.emit_expr(expr.args[0])
                return core.emit_fixed_array_fill(codegen, array_ptr, receiver_type, fill_value)

            case "reverse":
                # Fixed array reverse: reverse in-place
                # Need to get pointer to the array variable for in-place modification
                # Locals-only by design -- see the note under "fill" above.
                from sushi_lang.semantics.ast import Name
                if isinstance(expr.receiver, Name):
                    array_ptr = codegen.memory.find_local_slot(expr.receiver.id)
                else:
                    array_ptr = codegen.builder.alloca(receiver_type, name="temp_array")
                    codegen.builder.store(receiver_value, array_ptr)
                return core.emit_fixed_array_reverse(codegen, array_ptr, receiver_type)

            case _:
                raise NotImplementedError(f"Fixed array method not implemented: {method_name}")

    if isinstance(receiver_type, ir.PointerType):
        array_struct_type = receiver_type.pointee
    else:
        array_struct_type = receiver_type

    # The methods on `&T` are the methods on `T`. The receiver may be a `peek`/`poke`
    # array parameter, and the arms below read `semantic_type.base_type` for the element
    # type -- `.free()` and `.clone()` raise CE0042 on a miss, which is what made
    # `a.clone()` on a reference receiver unusable as CE2411's documented escape (#301).
    # Unwrapped ONCE here, where the dynamic-array section starts, rather than per arm:
    # `push` used to carry its own copy of this unwrap.
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
            return core.emit_dynamic_array_pop(codegen, receiver_value, array_struct_type, to_i1)

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
            fill_value = codegen.expressions.emit_expr(expr.args[0])
            return core.emit_dynamic_array_fill(codegen, receiver_value, array_struct_type, fill_value)

        case "reverse":
            return core.emit_dynamic_array_reverse(codegen, receiver_value, array_struct_type)

        case _:
            raise NotImplementedError(f"Dynamic array method not implemented: {method_name}")
