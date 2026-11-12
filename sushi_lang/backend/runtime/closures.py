"""Runtime support for closure / function values (the 3-word fat pointer)."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from llvmlite import ir

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import FunctionType, StructType
from sushi_lang.backend.functions.signature import CallSignature, call_signature
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.memory.heap import emit_malloc

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def null_ptr(codegen: "LLVMCodegen") -> ir.Constant:
    """A null `i8*` — the env/drop field of a non-capturing function value."""
    return ir.Constant(codegen.types.str_ptr, None)


def build_closure_value(
    codegen: "LLVMCodegen",
    fn_ptr: ir.Value,
    env_ptr: ir.Value,
    drop_ptr: ir.Value,
    clone_ptr: ir.Value,
) -> ir.Value:
    """Assemble a `{fn_ptr, env_ptr, drop_ptr, clone_ptr}` fat value from four i8* fields."""
    st = codegen.types.closure_struct
    v = ir.Constant(st, ir.Undefined)
    v = codegen.builder.insert_value(v, fn_ptr, 0)
    v = codegen.builder.insert_value(v, env_ptr, 1)
    v = codegen.builder.insert_value(v, drop_ptr, 2)
    v = codegen.builder.insert_value(v, clone_ptr, 3)
    return v


def _env_prepended_signature(codegen: "LLVMCodegen", fn_type: FunctionType) -> CallSignature:
    """The real callee signature: `R(i8* env, <params>)`, R the bare return or the channel
    Result, through the one signature rule (`call_signature`)."""
    from sushi_lang.backend.generics.result_builder import fn_value_result_type
    result_ll = codegen.types.ll_type(fn_value_result_type(codegen, fn_type))
    param_ll = [codegen.types.ll_type(p) for p in fn_type.param_types]
    return call_signature(result_ll, [codegen.types.str_ptr] + param_ll)


_THUNK_SUFFIX = ".__closure_thunk"


def synthesize_thunk(codegen: "LLVMCodegen", target: ir.Function) -> ir.Function:
    """Return (creating once, cached) the adapter thunk for a bare top-level fn."""
    target_ret = target.function_type.return_type
    target_params = list(target.function_type.args)
    thunk_ty = ir.FunctionType(target_ret, [codegen.types.str_ptr] + target_params)

    thunk_name = target.name + _THUNK_SUFFIX
    existing = codegen.module.globals.get(thunk_name)
    if isinstance(existing, ir.Function) and existing.function_type == thunk_ty:
        return existing

    thunk = ir.Function(codegen.module, thunk_ty, name=thunk_name)
    thunk.linkage = "internal"
    block = thunk.append_basic_block("entry")
    b = ir.IRBuilder(block)
    forwarded = list(thunk.args[1:])  # drop the leading env
    result = b.call(target, forwarded)
    if isinstance(target_ret, ir.VoidType):
        b.ret_void()
    else:
        b.ret(result)
    return thunk


def materialize_function_ref(
    codegen: "LLVMCodegen", target: ir.Function
) -> ir.Value:
    """Build a non-capturing `{thunk, null, null, null}` value for a bare fn reference."""
    thunk = synthesize_thunk(codegen, target)
    fn_ptr_i8 = codegen.builder.bitcast(thunk, codegen.types.str_ptr)
    nul = null_ptr(codegen)
    return build_closure_value(codegen, fn_ptr_i8, nul, nul, nul)


def closure_constant(codegen: "LLVMCodegen", symbol: str) -> ir.Constant:
    """The constant `{thunk, null, null, null}` of a function-typed constant.

    The function can be private to its unit, and a constant of its unit goes into the module
    of every unit. So the module that holds the private function exports its thunk, and every
    other module declares the thunk. A constant takes only the address of the thunk, so the
    declaration does not repeat its signature.
    """
    target = codegen.module.globals.get(symbol)
    if isinstance(target, ir.Function):
        thunk = synthesize_thunk(codegen, target)
        if target.linkage == "internal":
            thunk.linkage = "external"
    else:
        thunk = codegen.module.globals.get(symbol + _THUNK_SUFFIX)
        if thunk is None:
            thunk = ir.Function(codegen.module, ir.FunctionType(ir.VoidType(), []),
                                name=symbol + _THUNK_SUFFIX)
    nul = null_ptr(codegen)
    return ir.Constant(codegen.types.closure_struct,
                       [thunk.bitcast(codegen.types.str_ptr), nul, nul, nul])


def env_owns_field(codegen: "LLVMCodegen", field_type) -> bool:
    """Does the heap environment own this captured field?"""
    from sushi_lang.backend.ownership import drops_of, resolver_for
    from sushi_lang.semantics.ownership import TypeClass, type_class_of

    return type_class_of(field_type, drops_of(codegen),
                         resolver_for(codegen)) is not TypeClass.PLAIN


def get_or_create_env_drop(codegen: "LLVMCodegen", env_struct) -> ir.Function:
    """Return (creating once, cached) the type-erased env destructor for a closure."""
    from sushi_lang.backend.destructors import emit_value_destructor

    drop_name = f"{env_struct.name}.__closure_drop"
    drop_ty = ir.FunctionType(ir.VoidType(), [codegen.types.str_ptr])
    existing = codegen.module.globals.get(drop_name)
    if isinstance(existing, ir.Function) and existing.function_type == drop_ty:
        return existing

    fn = ir.Function(codegen.module, drop_ty, name=drop_name)
    fn.linkage = "linkonce_odr"
    b = ir.IRBuilder(fn.append_basic_block("entry"))
    env_i8 = fn.args[0]
    env_ll = codegen.types.ll_type(env_struct)
    env_ptr = b.bitcast(env_i8, ir.PointerType(env_ll), name="env_typed")

    # Destroy owned captured fields before freeing the buffer. The destructor helpers
    # reach for codegen.builder / codegen.func (loop blocks, element frees), so point
    # them at the drop fn while emitting, then restore the caller's context.
    owned = [(i, fty) for i, (_, fty) in enumerate(env_struct.fields)
             if env_owns_field(codegen, fty)]
    if owned:
        saved_builder, saved_func = codegen.builder, codegen.func
        codegen.builder, codegen.func = b, fn
        try:
            i32 = codegen.types.i32
            zero = ir.Constant(i32, 0)
            for idx, fty in owned:
                field_ptr = b.gep(env_ptr, [zero, ir.Constant(i32, idx)], inbounds=True, name="cap_field")
                if isinstance(fty, StructType) and is_instance_of(fty, "List"):
                    from sushi_lang.backend.generics.list.methods_destroy import emit_list_destroy
                    emit_list_destroy(codegen, field_ptr, fty)
                else:
                    emit_value_destructor(codegen, field_ptr, fty)
        finally:
            codegen.builder, codegen.func = saved_builder, saved_func

    free_func = codegen.get_free_func()
    b.call(free_func, [env_i8])
    b.ret_void()
    return fn


def get_or_create_env_clone(codegen: "LLVMCodegen", env_struct) -> ir.Function:
    """Return (creating once, cached) the type-erased env duplicator for a closure."""
    from sushi_lang.backend.ownership import copy_out

    clone_name = f"{env_struct.name}.__closure_clone"
    clone_ty = ir.FunctionType(codegen.types.str_ptr, [codegen.types.str_ptr])
    existing = codegen.module.globals.get(clone_name)
    if isinstance(existing, ir.Function) and existing.function_type == clone_ty:
        return existing

    fn = ir.Function(codegen.module, clone_ty, name=clone_name)
    fn.linkage = "linkonce_odr"
    b = ir.IRBuilder(fn.append_basic_block("entry"))
    env_i8 = fn.args[0]
    env_ll = codegen.types.ll_type(env_struct)
    env_ptr_ty = ir.PointerType(env_ll)

    # The clone helpers reach for the AMBIENT codegen.builder / codegen.func (they append
    # blocks for element loops and string copies), so swap both while emitting this body
    # and restore afterwards -- the same discipline get_or_create_env_drop follows, and
    # the one #257 documents as load-bearing for out-of-line bodies.
    saved_builder, saved_func = codegen.builder, codegen.func
    codegen.builder, codegen.func = b, fn
    try:
        size = codegen.types.get_type_size_bytes(env_struct)
        raw = emit_malloc(codegen, b, ir.Constant(codegen.types.i64, size))
        new_ptr = b.bitcast(raw, env_ptr_ty, name="cloned_env")
        old_ptr = b.bitcast(env_i8, env_ptr_ty, name="src_env")

        b.store(b.load(old_ptr, name="src_env_val"), new_ptr)

        i32 = codegen.types.i32
        zero = ir.Constant(i32, 0)
        for idx, (_name, fty) in enumerate(env_struct.fields):
            if not env_owns_field(codegen, fty):
                continue
            field_ptr = b.gep(new_ptr, [zero, ir.Constant(i32, idx)],
                              inbounds=True, name="clone_cap_field")
            orig = codegen.builder.load(field_ptr, name="clone_cap_orig")
            codegen.builder.store(copy_out(codegen, orig, fty), field_ptr)

        codegen.builder.ret(raw)
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn


def emit_capturing_closure(codegen: "LLVMCodegen", target: ir.Function, env_struct: StructType,
                           parts: List[ir.Value]) -> ir.Value:
    """A closure that captures: ONE heap block for its environment, with each part stored in
    slot order, and the glue of its layout. The environment owns what the parts hold."""
    builder = codegen.builder
    size = codegen.types.get_type_size_bytes(env_struct)
    raw = emit_malloc(codegen, builder, ir.Constant(codegen.types.i64, size))
    env_ptr = builder.bitcast(raw, ir.PointerType(codegen.types.ll_type(env_struct)),
                              name="closure_env_typed")
    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)
    for index, part in enumerate(parts):
        builder.store(part, builder.gep(env_ptr, [zero, ir.Constant(i32, index)], inbounds=True))
    str_ptr = codegen.types.str_ptr
    return build_closure_value(codegen, builder.bitcast(target, str_ptr), raw,
                               builder.bitcast(get_or_create_env_drop(codegen, env_struct), str_ptr),
                               builder.bitcast(get_or_create_env_clone(codegen, env_struct), str_ptr))


def emit_empty_closure(codegen: "LLVMCodegen", target: ir.Function) -> ir.Value:
    """A lambda that captures nothing: the lifted function takes its environment first, and it
    never reads the null that comes. The glue does nothing."""
    nul = null_ptr(codegen)
    return build_closure_value(codegen, codegen.builder.bitcast(target, codegen.types.str_ptr),
                               nul, nul, nul)


def emit_indirect_call(
    codegen: "LLVMCodegen",
    fat_value: ir.Value,
    fn_type: FunctionType,
    arg_values: List[ir.Value],
    to_i1: bool,
) -> ir.Value:
    """Call through a function value, threading `env_ptr` as the hidden leading arg. An
    answer that moves by address is read from a new entry-block slot."""
    signature = _env_prepended_signature(codegen, fn_type)
    if signature.answer_by_address:
        slot = entry_alloca(codegen.builder, signature.answer, "answer")
        _emit_indirect(codegen, signature, fat_value, arg_values, slot)
        return codegen.builder.load(slot)
    result = _emit_indirect(codegen, signature, fat_value, arg_values, None)
    return codegen.utils.as_i1(result) if to_i1 else result


def emit_indirect_call_into(codegen: "LLVMCodegen", fat_value: ir.Value, fn_type: FunctionType,
                            arg_values: List[ir.Value], dest: ir.Value) -> None:
    """Call through a function value, and put the answer at the address `dest`: the callee
    writes it there when it moves by address."""
    signature = _env_prepended_signature(codegen, fn_type)
    result = _emit_indirect(codegen, signature, fat_value, arg_values,
                            dest if signature.answer_by_address else None)
    if not signature.answer_by_address:
        codegen.builder.store(result, dest)


def _emit_indirect(codegen: "LLVMCodegen", signature: CallSignature, fat_value: ir.Value,
                   arg_values: List[ir.Value], answer_slot: Optional[ir.Value]) -> ir.Value:
    """The call instruction. An argument at a position that crosses by address is its
    address, or a value that goes into a slot first. `answer_slot` is the last argument
    when the answer moves by address."""
    callee_ty = signature.function_type
    fn_ptr_i8 = codegen.builder.extract_value(fat_value, 0)
    env_ptr = codegen.builder.extract_value(fat_value, 1)
    callee = codegen.builder.bitcast(fn_ptr_i8, ir.PointerType(callee_ty))

    param_ll = list(callee_ty.args)[1:len(arg_values) + 1]  # skip the env slot
    casted = [_by_address(codegen, v, pt) if moved else codegen.utils.cast_for_param(v, pt)
              for v, pt, moved in zip(arg_values, param_ll, signature.by_address[1:],
                                      strict=True)]
    tail = [] if answer_slot is None else [answer_slot]
    return codegen.builder.call(callee, [env_ptr] + casted + tail)


def _by_address(codegen: "LLVMCodegen", value: ir.Value, param: ir.Type) -> ir.Value:
    """The address that a by-address parameter takes: `value` itself when it is one, else a
    new entry-block slot that holds it."""
    if value.type == param:
        return value
    slot = entry_alloca(codegen.builder, value.type, "arg")
    codegen.builder.store(value, slot)
    return slot
