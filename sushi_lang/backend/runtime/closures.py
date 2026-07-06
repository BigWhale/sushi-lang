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

from sushi_lang.semantics.typesys import FunctionType, ResultType

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
) -> ir.Value:
    """Assemble a `{fn_ptr, env_ptr, drop_ptr}` fat value from three i8* fields.

    All three arguments must already be `i8*`. Uses the same insert_value idiom the
    string fat pointer uses (a pure SSA value, no alloca).
    """
    st = codegen.types.closure_struct
    v = ir.Constant(st, ir.Undefined)
    v = codegen.builder.insert_value(v, fn_ptr, 0)
    v = codegen.builder.insert_value(v, env_ptr, 1)
    v = codegen.builder.insert_value(v, drop_ptr, 2)
    return v


def _env_prepended_signature(codegen: "LLVMCodegen", fn_type: FunctionType) -> ir.FunctionType:
    """The real callee signature: `Result<T,E>(i8* env, <params>)`.

    Recovered from the semantic `FunctionType` because the stored `fn_ptr` is an
    opaque `i8*` that has erased its own signature.
    """
    result_ll = codegen.types.ll_type(
        ResultType(ok_type=fn_type.ok_type, err_type=fn_type.err_type)
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
    """Build a non-capturing `{thunk, null, null}` value for a bare fn reference."""
    thunk = synthesize_thunk(codegen, target)
    fn_ptr_i8 = codegen.builder.bitcast(thunk, codegen.types.str_ptr)
    return build_closure_value(codegen, fn_ptr_i8, null_ptr(codegen), null_ptr(codegen))


def emit_lambda(codegen: "LLVMCodegen", lam, to_i1: bool) -> ir.Value:
    """Materialize a lambda literal as a function value at its use site.

    Non-capturing: `{&__lambda_N, null, null}` (the lifted fn ignores its env slot).
    Capturing: heap-allocate the environment struct, copy each captured value into it,
    and build `{&__lambda_N, env_ptr, drop_ptr}`. The `drop_ptr` is null in T1.4/T1.6;
    T1.5 wires the env destructor here so RAII frees the environment.
    """
    from sushi_lang.internals.errors import raise_internal_error
    from sushi_lang.semantics.ast import Name as _Name

    lifted = codegen.funcs.get(getattr(lam, "lifted_name", None))
    if lifted is None:
        raise_internal_error("CE0055", name=str(getattr(lam, "lifted_name", "<lambda>")))

    fn_ptr_i8 = codegen.builder.bitcast(lifted, codegen.types.str_ptr)
    captures = lam.captures or []
    if not captures:
        # No environment: pass a null env; the lifted body never reads it.
        return build_closure_value(codegen, fn_ptr_i8, null_ptr(codegen), null_ptr(codegen))

    # Heap-allocate the environment struct and copy captured values into it.
    env_struct = lam.env_struct
    env_ll = codegen.types.ll_type(env_struct)
    size = codegen.types.get_type_size_bytes(env_struct)
    malloc = codegen.get_malloc_func()
    raw = codegen.builder.call(malloc, [ir.Constant(codegen.types.i64, size)], name="closure_env")
    env_ptr = codegen.builder.bitcast(raw, ir.PointerType(env_ll), name="closure_env_typed")

    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)
    for idx, cap in enumerate(captures):
        value = codegen.expressions.emit_expr(_Name(id=cap.name, loc=lam.loc))
        field_ptr = codegen.builder.gep(env_ptr, [zero, ir.Constant(i32, idx)], inbounds=True)
        codegen.builder.store(value, field_ptr)

    env_i8 = codegen.builder.bitcast(env_ptr, codegen.types.str_ptr)
    return build_closure_value(codegen, fn_ptr_i8, env_i8, null_ptr(codegen))


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
    casted = [codegen.utils.cast_for_param(v, pt) for v, pt in zip(arg_values, param_ll)]
    result = codegen.builder.call(callee, [env_ptr] + casted)
    return codegen.utils.as_i1(result) if to_i1 else result
