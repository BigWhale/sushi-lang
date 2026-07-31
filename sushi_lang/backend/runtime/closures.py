"""Runtime support for closure / function values (the 3-word fat pointer).

A function value lowers to `{i8* fn_ptr, i8* env_ptr, i8* drop_ptr}` (see
`backend/types/core/mapping.py`). This module holds the small, stable API for
building and calling through that value, mirroring the string fat-pointer idioms
in `runtime/strings.py`:

- `build_closure_value` — assemble the fat struct from its three i8* fields.
- `synthesize_thunk` — the adapter-thunk split: bridge a bare top-level fn into
  the uniform env-passing indirect ABI (once, cached).
- `materialize_function_ref` — a bare fn reference as a `{thunk, null, null}` value.
- `emit_indirect_call` — call through a fat value, threading `env_ptr` as the
  hidden leading argument, recovering the real callee signature from the semantic
  `FunctionType` (the opaque `i8* fn_ptr` carries no signature of its own).

T1.0 uses only the non-capturing paths (null env/drop); the capturing lambda
layer (T1.4-T1.8) reuses `build_closure_value` and `emit_indirect_call` verbatim,
which is why they are a named API rather than inlined IR at each site.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List

from llvmlite import ir

from sushi_lang.semantics.typesys import FunctionType
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
    """Assemble a `{fn_ptr, env_ptr, drop_ptr, clone_ptr}` fat value from four i8* fields.

    All four arguments must already be `i8*`. Uses the same insert_value idiom the
    string fat pointer uses (a pure SSA value, no alloca).
    """
    st = codegen.types.closure_struct
    v = ir.Constant(st, ir.Undefined)
    v = codegen.builder.insert_value(v, fn_ptr, 0)
    v = codegen.builder.insert_value(v, env_ptr, 1)
    v = codegen.builder.insert_value(v, drop_ptr, 2)
    v = codegen.builder.insert_value(v, clone_ptr, 3)
    return v


def _env_prepended_signature(codegen: "LLVMCodegen", fn_type: FunctionType) -> ir.FunctionType:
    """The real callee signature: `Result<T,E>(i8* env, <params>)`.

    Recovered from the semantic `FunctionType` because the stored `fn_ptr` is an
    opaque `i8*` that has erased its own signature.
    """
    from sushi_lang.backend.generics.result_builder import intern_result
    result_ll = codegen.types.ll_type(
        intern_result(codegen, fn_type.ok_type, fn_type.err_type)
    )
    param_ll = [codegen.types.ll_type(p) for p in fn_type.param_types]
    return ir.FunctionType(result_ll, [codegen.types.str_ptr] + param_ll)


def synthesize_thunk(codegen: "LLVMCodegen", target: ir.Function) -> ir.Function:
    """Return (creating once, cached) the adapter thunk for a bare top-level fn.

    `target` has the direct-call signature `Result<T,E>(<params>)`. The thunk

        Result<T,E> target.__closure_thunk(i8* env, <params>) { return target(<params>) }

    ignores `env` and forwards, so a bare fn plugs into the uniform env-passing
    indirect ABI without touching the real function body.

    The thunk symbol embeds a `.`, which Sushi's `NAME` (CNAME) token can never
    produce, so it cannot collide with a user function. As a second guard, a cache
    hit is trusted only when the existing global's signature matches the expected
    thunk signature.
    """
    target_ret = target.function_type.return_type
    target_params = list(target.function_type.args)
    thunk_ty = ir.FunctionType(target_ret, [codegen.types.str_ptr] + target_params)

    thunk_name = f"{target.name}.__closure_thunk"
    existing = codegen.module.globals.get(thunk_name)
    if isinstance(existing, ir.Function) and existing.function_type == thunk_ty:
        return existing

    thunk = ir.Function(codegen.module, thunk_ty, name=thunk_name)
    thunk.linkage = "internal"
    # Emit the body with a private builder so the caller's builder position is
    # untouched (we may be mid-emit inside another function).
    block = thunk.append_basic_block("entry")
    b = ir.IRBuilder(block)
    forwarded = list(thunk.args[1:])  # drop the leading env
    result = b.call(target, forwarded)
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


def env_owns_field(codegen: "LLVMCodegen", field_type) -> bool:
    """Does the heap environment own this captured field?

    The environment owns exactly what the seam gave it at the capture. A MOVE type moved
    in, and any other heap-owning type arrived as a deep copy. Both belong to the
    environment, so both must be destroyed with it and duplicated when it is cloned.

    So the gate is the classification `consume` already applied at `ConsumingUse.CAPTURE`,
    not a narrower predicate of its own. Take fewer fields than the capture gave and the
    environment leaks them; take more and it frees storage another owner still holds.
    """
    from sushi_lang.backend.ownership import resolver_for
    from sushi_lang.semantics.ownership import TypeClass, type_class_of

    return type_class_of(field_type, resolver_for(codegen)) is not TypeClass.PLAIN


def get_or_create_env_drop(codegen: "LLVMCodegen", env_struct) -> ir.Function:
    """Return (creating once, cached) the type-erased env destructor for a closure.

        void __closure_env_N.__closure_drop(i8* env):
            <destroy each owned captured field>
            free(env)

    Its address is stored in the fat value's `drop_ptr` slot; RAII frees the env by
    calling through it (guarded by `drop_ptr != null`). Only *owned* captured fields
    (move-captured dynamic arrays / `List<T>` / `Own<T>` / nested capturing closures)
    are destroyed -- copy-captured primitives/strings/copyable structs share nothing
    with the env, so the env just frees its buffer. The `.` in the symbol cannot appear
    in a Sushi `NAME`, so it never collides with a user function; a cache hit also
    re-checks the signature (mirroring `synthesize_thunk`).
    """
    from sushi_lang.backend.destructors import emit_value_destructor

    drop_name = f"{env_struct.name}.__closure_drop"
    drop_ty = ir.FunctionType(ir.VoidType(), [codegen.types.str_ptr])
    existing = codegen.module.globals.get(drop_name)
    if isinstance(existing, ir.Function) and existing.function_type == drop_ty:
        return existing

    fn = ir.Function(codegen.module, drop_ty, name=drop_name)
    fn.linkage = "internal"
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
                da = codegen.dynamic_arrays
                if da is not None and da.is_list_type(fty):
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
    """Return (creating once, cached) the type-erased env duplicator for a closure.

        i8* __closure_env_N.__closure_clone(i8* env):
            new = malloc(sizeof env)
            *new = *env                       # copy every field
            <deep-copy each field the drop fn would destroy>
            return new

    Exact structural inverse of `get_or_create_env_drop`, and cached and named the same
    way. Its address rides in the fat value's `clone_ptr` slot, which is the only way a
    closure reached through a struct field or a container can be deep-copied at all: the
    `fn(...)` type has erased its captures, so the layout is not recoverable at the clone
    site.

    The gate MUST be the one `get_or_create_env_drop` uses -- `env_owns_field`. Clone
    fewer fields than the drop frees and the shared buffer is freed twice; clone more and
    the extra copy is never freed. The two gates are one decision.
    """
    from sushi_lang.backend.expressions.memory import emit_value_clone

    clone_name = f"{env_struct.name}.__closure_clone"
    clone_ty = ir.FunctionType(codegen.types.str_ptr, [codegen.types.str_ptr])
    existing = codegen.module.globals.get(clone_name)
    if isinstance(existing, ir.Function) and existing.function_type == clone_ty:
        return existing

    fn = ir.Function(codegen.module, clone_ty, name=clone_name)
    fn.linkage = "internal"
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

        # Shallow-copy first so non-owning captures carry over untouched, then deep-copy
        # exactly the fields the drop fn destroys.
        b.store(b.load(old_ptr, name="src_env_val"), new_ptr)

        i32 = codegen.types.i32
        zero = ir.Constant(i32, 0)
        for idx, (_name, fty) in enumerate(env_struct.fields):
            if not env_owns_field(codegen, fty):
                continue
            field_ptr = b.gep(new_ptr, [zero, ir.Constant(i32, idx)],
                              inbounds=True, name="clone_cap_field")
            orig = codegen.builder.load(field_ptr, name="clone_cap_orig")
            codegen.builder.store(emit_value_clone(codegen, orig, fty), field_ptr)

        codegen.builder.ret(raw)
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn


def emit_lambda(codegen: "LLVMCodegen", lam, to_i1: bool) -> ir.Value:
    """Materialize a lambda literal as a function value at its use site.

    Non-capturing: `{&__lambda_N, null, null}` (the lifted fn ignores its env slot).
    Capturing: heap-allocate the environment struct, give each captured value to it
    through the ownership seam, and build `{&__lambda_N, env_ptr, drop_ptr}` where
    `drop_ptr` is the env destructor so RAII frees the environment.
    """
    from sushi_lang.internals.errors import raise_internal_error
    from sushi_lang.semantics.ast import Name as _Name
    from sushi_lang.backend.ownership import ConsumingUse, consume

    lifted = codegen.funcs.get(getattr(lam, "lifted_name", None))
    if lifted is None:
        raise_internal_error("CE0055", name=str(getattr(lam, "lifted_name", "<lambda>")))

    fn_ptr_i8 = codegen.builder.bitcast(lifted, codegen.types.str_ptr)
    captures = lam.captures or []
    if not captures:
        # No environment: pass a null env; the lifted body never reads it.
        nul = null_ptr(codegen)
        return build_closure_value(codegen, fn_ptr_i8, nul, nul, nul)

    # Heap-allocate the environment struct and populate captured values into it.
    env_struct = lam.env_struct
    env_ll = codegen.types.ll_type(env_struct)
    size = codegen.types.get_type_size_bytes(env_struct)
    raw = emit_malloc(codegen, codegen.builder, ir.Constant(codegen.types.i64, size))
    env_ptr = codegen.builder.bitcast(raw, ir.PointerType(env_ll), name="closure_env_typed")

    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)
    for idx, cap in enumerate(captures):
        # A capture names a variable but holds no source `Expr`, so Pass 3 puts the
        # provenance on the `Param`. Carry it onto the synthesized `Name` the seam reads.
        source = _Name(id=cap.name, loc=lam.loc)
        source.ownership_provenance = getattr(cap, "ownership_provenance", None)
        value = codegen.expressions.emit_expr(source)
        # The environment takes ownership of the captured value and outlives the scope
        # that built it. That makes this the CAPTURE consuming use, with no decision of
        # its own -- `env_owns_field` then destroys exactly what the seam gave it here.
        value = consume(codegen, source, value, cap.ty, ConsumingUse.CAPTURE)
        field_ptr = codegen.builder.gep(env_ptr, [zero, ir.Constant(i32, idx)], inbounds=True)
        codegen.builder.store(value, field_ptr)

    env_i8 = codegen.builder.bitcast(env_ptr, codegen.types.str_ptr)
    drop_fn = get_or_create_env_drop(codegen, env_struct)
    drop_i8 = codegen.builder.bitcast(drop_fn, codegen.types.str_ptr)
    clone_fn = get_or_create_env_clone(codegen, env_struct)
    clone_i8 = codegen.builder.bitcast(clone_fn, codegen.types.str_ptr)
    return build_closure_value(codegen, fn_ptr_i8, env_i8, drop_i8, clone_i8)


def emit_indirect_call(
    codegen: "LLVMCodegen",
    fat_value: ir.Value,
    fn_type: FunctionType,
    arg_values: List[ir.Value],
    to_i1: bool,
) -> ir.Value:
    """Call through a function value, threading `env_ptr` as the hidden leading arg.

    `fat_value` is the `{fn_ptr, env_ptr, drop_ptr}` struct value; `fn_type` is its
    resolved semantic type (source of the real callee signature); `arg_values` are
    the already-emitted call arguments. Returns the `Result<T,E>` struct (or its i1
    truthiness when `to_i1`), exactly like a direct call.
    """
    callee_ty = _env_prepended_signature(codegen, fn_type)
    fn_ptr_i8 = codegen.builder.extract_value(fat_value, 0)
    env_ptr = codegen.builder.extract_value(fat_value, 1)
    callee = codegen.builder.bitcast(fn_ptr_i8, ir.PointerType(callee_ty))

    param_ll = list(callee_ty.args)[1:]  # skip the env slot
    casted = [codegen.utils.cast_for_param(v, pt) for v, pt in zip(arg_values, param_ll, strict=True)]
    result = codegen.builder.call(callee, [env_ptr] + casted)
    return codegen.utils.as_i1(result) if to_i1 else result
