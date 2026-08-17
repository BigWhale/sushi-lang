"""Main call dispatcher for function and method calls."""
from __future__ import annotations
import itertools
from typing import TYPE_CHECKING, Union

from llvmlite import ir
from sushi_lang.semantics.ast import Call, MethodCall, DotCall, Name
from sushi_lang.backend.expressions.calls.file_open import emit_open_function
from sushi_lang.backend.expressions.calls.stdlib import emit_time_function, emit_math_function, emit_env_function
from sushi_lang.backend.expressions.calls import intrinsics, generics
from sushi_lang.backend.expressions.calls.utils import emit_receiver_value
from sushi_lang.backend.expressions.calls.variadic import build_variadic_array
from sushi_lang.backend.ownership import ConsumingUse, consume
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_function_call(codegen: 'LLVMCodegen', expr: Call, to_i1: bool) -> ir.Value:
    """Emit function call with argument type casting."""
    # Call-through any expression yielding a function value. Pass 2 annotated the resolved
    # FunctionType, so emit the callee to a fat value and dispatch indirectly.
    if not isinstance(expr.callee, Name):
        from sushi_lang.semantics.typesys import FunctionType
        fn_type = expr.callee_fn_type
        if not isinstance(fn_type, FunctionType):
            raise_internal_error("CE0027", type=type(expr.callee).__name__)
        fat_value = codegen.expressions.emit_expr(expr.callee)
        return _emit_indirect_call(codegen, expr, fat_value, fn_type, to_i1)

    callee = expr.callee.id

    fn_value = _try_function_value_local(codegen, callee)
    if fn_value is not None:
        fat_value, fn_type = fn_value
        return _emit_indirect_call(codegen, expr, fat_value, fn_type, to_i1)

    if callee in codegen.struct_table.by_name:
        from sushi_lang.backend.expressions import structs
        return structs.emit_struct_constructor(codegen, expr, to_i1)

    if hasattr(codegen, 'generic_structs') and callee in codegen.generic_structs.by_name:
        from sushi_lang.backend.expressions import structs
        return structs.emit_struct_constructor(codegen, expr, to_i1)

    if callee == "open":
        return emit_open_function(codegen, expr, to_i1)

    stdlib_func = _check_stdlib_function_codegen(codegen, callee)
    if stdlib_func is not None:
        return _emit_stdlib_function(codegen, expr, callee, stdlib_func, to_i1)

    llvm_fn = codegen.funcs.get(callee)
    if llvm_fn is None:
        raise KeyError(f"unknown function: {callee}")

    # Native variadic call: collapse the trailing arguments into one synthesized,
    # owned T[] which is moved into the callee. Must happen BEFORE the arity guard
    # so the produced argument count matches the (non-variadic) LLVM signature.
    func_sig = codegen.func_table.by_name.get(callee)
    variadic_param = (
        func_sig.params[-1]
        if func_sig is not None and func_sig.params
        and getattr(func_sig.params[-1], "is_variadic", False)
        else None
    )

    if variadic_param is not None:
        fixed_count = len(func_sig.params) - 1
        fixed_args = [codegen.expressions.emit_expr(a) for a in expr.args[:fixed_count]]
        # The FIXED parameters follow their declared modes like any call's. The trailing
        # arguments go into the synthesized T[], which the callee owns whole.
        _settle_named_call_arguments(codegen, expr.args[:fixed_count], fixed_args, func_sig)
        array_struct = build_variadic_array(
            codegen, expr.args[fixed_count:], variadic_param.ty, callee)
        args = fixed_args + [array_struct]
    else:
        args = [codegen.expressions.emit_expr(a) for a in expr.args]
        _settle_named_call_arguments(codegen, expr.args, args, func_sig)

    params = list(llvm_fn.args)
    if len(args) != len(params):
        raise_internal_error("CE0026", expected=len(params), got=len(args))

    # Normalize a by-pointer owning argument against a by-value struct parameter, or
    # cast_for_param raises CE0017 (#131). Fires only on an exact pointer-to-value-struct
    # mismatch, so a peek/poke pointer param never triggers it. BaseStructType, because a
    # user struct's identified type is a SIBLING of LiteralStructType (#257).
    args = [
        codegen.builder.load(v, name="arg_by_value")
        if isinstance(p.type, ir.types.BaseStructType) and v.type == ir.PointerType(p.type)
        else v
        for v, p in zip(args, params, strict=True)
    ]

    casted = [codegen.utils.cast_for_param(v, p.type) for v, p in zip(args, params, strict=True)]
    result_struct = codegen.builder.call(llvm_fn, casted)

    # Functions now return Result<T> as enum: {i32 tag, [N x i8] data}
    # Return the full Result<T> struct - downstream code will handle extraction
    # (e.g., .realise() method, if (result) conditionals, etc.)
    return codegen.utils.as_i1(result_struct) if to_i1 else result_struct


def emit_fn_field_call(codegen: 'LLVMCodegen', expr: DotCall, fn_type, to_i1: bool) -> ir.Value:
    """Emit `obj.handler(args)` as an indirect call through the fn-typed field `handler`."""
    from sushi_lang.semantics.ast import MemberAccess
    field_access = MemberAccess(receiver=expr.receiver, member=expr.method, loc=expr.loc)
    fat_value = codegen.expressions.emit_expr(field_access)
    return _emit_indirect_call(codegen, expr, fat_value, fn_type, to_i1)


def _try_function_value_local(codegen: 'LLVMCodegen', name: str):
    """If `name` is a function-valued local, return `(fat_value, FunctionType)`, else None."""
    from sushi_lang.semantics.typesys import FunctionType
    slot = codegen.memory.try_find_local_slot(name)
    if slot is None:
        return None
    sem_ty = codegen.memory.find_semantic_type(name)
    if not isinstance(sem_ty, FunctionType):
        return None
    fat_value = codegen.builder.load(slot, name=f"{name}_fnval")
    return fat_value, sem_ty


def _emit_indirect_call(codegen: 'LLVMCodegen', expr: Call, fat_value: 'ir.Value',
                        fn_type, to_i1: bool) -> ir.Value:
    """Emit an indirect call through a function value (fat pointer)."""
    from sushi_lang.backend.runtime import closures
    from sushi_lang.semantics.param_modes import CalleeKind, effective_modes
    args = [codegen.expressions.emit_expr(a) for a in expr.args]
    # The callee's modes travel WITH the function type, which is why the type is
    # invariant on them: without that, one indirection would defeat the rule (#335).
    settle_call_arguments(
        codegen, list(expr.args), args, list(fn_type.param_types),
        effective_modes(fn_type.modes, CalleeKind.INDIRECT))
    return closures.emit_indirect_call(codegen, fat_value, fn_type, args, to_i1)


def _resolve_param_type(codegen: 'LLVMCodegen', ty):
    """Resolve an UnknownType param name to its concrete StructType/EnumType."""
    from sushi_lang.semantics.typesys import UnknownType
    if isinstance(ty, UnknownType):
        return (codegen.struct_table.by_name.get(ty.name)
                or codegen.enum_table.by_name.get(ty.name)
                or ty)
    return ty


_ARG_TEMP_SEQ = itertools.count()


def _park_argument_temp(codegen: 'LLVMCodegen', value: ir.Value, resolved) -> None:
    """Give a caller-kept argument temporary an owner, so scope exit frees it once."""
    ll_type = codegen.types.ll_type(resolved)
    if isinstance(value.type, ir.PointerType) and value.type.pointee == ll_type:
        value = codegen.builder.load(value, name="arg_temp_val")
    elif value.type != ll_type:
        return

    name = f"__arg_temp_{next(_ARG_TEMP_SEQ)}"
    slot = codegen.memory.create_local(name, value.type, value, resolved,
                                       register_cleanup=False)
    codegen.memory.register_owning_value(name, resolved, slot)


def settle_call_arguments(codegen: 'LLVMCodegen', arg_exprs: list, args: list,
                          param_types: list, modes) -> None:
    """THE call-argument seam: give every argument exactly one owner, in place."""
    from sushi_lang.backend.destructors import needs_cleanup
    from sushi_lang.backend.expressions.memory import expression_is_temporary

    for i, mode in enumerate(modes):
        if i >= len(args) or i >= len(arg_exprs):
            continue
        arg_expr = arg_exprs[i]
        if arg_expr is None:
            continue
        resolved = _resolve_param_type(codegen, param_types[i])
        if mode.consumes:
            args[i] = consume(codegen, arg_expr, args[i], resolved,
                              ConsumingUse.CALL_ARG)
        elif (resolved is not None and needs_cleanup(resolved)
                and expression_is_temporary(codegen, arg_expr)):
            _park_argument_temp(codegen, args[i], resolved)


def _settle_method_call_arguments(codegen: 'LLVMCodegen', expr, args: list) -> None:
    """Settle a method call's arguments from the modes Pass 2 resolved."""
    modes = getattr(expr, "callee_param_modes", None)
    if modes is None:
        return
    param_types = list(getattr(expr, "callee_param_types", None) or ())
    if len(param_types) < len(modes):
        param_types += [None] * (len(modes) - len(param_types))
    settle_call_arguments(codegen, list(expr.args), args, param_types, modes)


def _settle_named_call_arguments(codegen: 'LLVMCodegen', arg_exprs: list, args: list,
                                 func_sig) -> None:
    """Settle the ownership of a named callee's arguments, from its declared signature."""
    from sushi_lang.semantics.param_modes import CalleeKind, modes_for
    if func_sig is None or not func_sig.params:
        return
    settle_call_arguments(
        codegen, arg_exprs, args,
        [p.ty for p in func_sig.params],
        modes_for(func_sig.params, CalleeKind.FUNCTION))


def emit_method_call(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool = False, is_dotcall: bool = False) -> ir.Value:
    """Emit a method call as a function call with the receiver as first argument (UFCS).

    Every built-in is resolved BEFORE the user-extension fallback, which is why an extension
    method whose name collides with a built-in can never run -- Pass 2 rejects one as CE2097
    rather than letting it be emitted and never called. The same precedence is implemented in
    validation and inference; see docs/design/method-resolution.md.
    """
    # Priority-ordered: each handler returns a value if it matches, or None to continue.
    # 0. FFI: foreign namespace call (libc.strlen(...)) - resolved by the type
    #    checker via the external_ref annotation. Direct, raw C call.
    result = _try_emit_external_call(codegen, expr)
    if result is not None:
        return result

    result = intrinsics.try_emit_enum_constructor(codegen, expr)
    if result is not None:
        return result

    result = intrinsics.try_emit_struct_constructor(codegen, expr)
    if result is not None:
        return result

    result = intrinsics.try_emit_stdio_method(codegen, expr, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_file_method(codegen, expr, to_i1)
    if result is not None:
        return result

    # 5. Result<T, E> and Maybe<T> methods (is_ok, is_some, realise, expect, ...).
    #    One handler, not two: `realise` and `expect` are in both method sets, and trying the
    #    families in sequence emitted the receiver once per attempt (#199).
    result = generics.try_emit_result_or_maybe_method(codegen, expr, to_i1)
    if result is not None:
        return result

    result = generics.try_emit_own_method(codegen, expr, to_i1)
    if result is not None:
        return result

    result = generics.try_emit_hashmap_method(codegen, expr, to_i1)
    if result is not None:
        return result

    result = generics.try_emit_list_method(codegen, expr, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_primitive_static(codegen, expr, to_i1)
    if result is not None:
        return result

    receiver_value, receiver_type, semantic_type = emit_receiver_value(codegen, expr.receiver)

    result = intrinsics.try_emit_array_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_string_method(codegen, expr, receiver_value, receiver_type, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_perk_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_struct_hash(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_enum_hash(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 14a. Auto-derived struct clone (#134)
    result = intrinsics.try_emit_struct_clone(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 14b. Auto-derived enum clone (#134)
    result = intrinsics.try_emit_enum_clone(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # 14c. Function-value clone -- a closure read out of a field or a container is a
    # borrow, so `.clone()` is CE2411's escape. Without this arm dispatch reached the
    # extension fallback, which mangled the type name into `fn(i32) - i32_clone`.
    result = intrinsics.try_emit_function_clone(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    result = intrinsics.try_emit_primitive_method(codegen, expr, receiver_value, receiver_type, semantic_type, to_i1)
    if result is not None:
        return result

    # Fallback: user-defined extension methods. The semantic type distinguishes bool
    # from i8.
    if semantic_type is not None:
        from sushi_lang.semantics.typesys import deref_type
        actual_type = deref_type(semantic_type)
        lang_type = str(actual_type)
    else:
        lang_type = codegen.types.map_llvm_to_language_type(receiver_type)

    sanitized_lang_type = lang_type.replace("<", "__").replace(">", "").replace(", ", "_")
    func_name = f"{sanitized_lang_type}_{expr.method}"
    llvm_fn = codegen.funcs.get(func_name)

    if llvm_fn is None and func_name in codegen.module.globals:
        llvm_fn = codegen.module.globals[func_name]

    if llvm_fn is None and lang_type == "string":
        from sushi_lang.backend.functions import declare_stdlib_function
        from sushi_lang.sushi_stdlib.src.collections.strings import get_builtin_string_method_return_type
        from sushi_lang.semantics.typesys import BuiltinType

        ret_sushi_type = get_builtin_string_method_return_type(expr.method, BuiltinType.STRING)
        if ret_sushi_type is not None:
            ret_llvm_type = codegen.types.ll_type(ret_sushi_type)
            llvm_fn = declare_stdlib_function(codegen.module, func_name, ret_llvm_type, [receiver_type])

    if llvm_fn is None:
        raise KeyError(f"Extension method not found: {func_name}")

    # A `poke self` / `peek self` method (#327) takes its receiver by POINTER, so a
    # write through `self` reaches the caller's value. Pass 2 stamped the resolution on
    # the node; `emit_receiver_as_pointer` returns the receiver's slot address (with the
    # load-through for a reference-parameter receiver). Pass 2/3 reject the shapes with
    # no address (a temporary, a constant, a read-only root) before codegen.
    if getattr(expr, "callee_self_mode", None) is not None:
        from sushi_lang.backend.expressions.calls.utils import emit_receiver_as_pointer
        receiver_value = emit_receiver_as_pointer(codegen, expr.receiver)

    emitted_args = [receiver_value]
    arg_values = [codegen.expressions.emit_expr(arg) for arg in expr.args]
    # A method's arguments follow the declared modes exactly like a plain call's: a
    # `nom` one transfers, and every other one stays the caller's -- which is what
    # registers an unbound owning temporary, so `b.eat(make_list())` is freed once.
    # That was `_register_inline_closure_temps`, which covered a syntactic `Lambda`
    # argument only and leaked every other temporary shape.
    _settle_method_call_arguments(codegen, expr, arg_values)
    emitted_args.extend(arg_values)

    params = list(llvm_fn.args)
    if len(emitted_args) != len(params):
        raise_internal_error("CE0026", expected=len(params), got=len(emitted_args))

    # Reconcile a by-pointer receiver against a by-value `self` parameter (#124), or
    # `cast_for_param` raises CE0017. The receiver only: a peek/poke param has a pointer
    # param type, so this never misfires. The by-value `self` shallow-copies the caller's
    # `data*`, and an extension body registers no cleanup, so there is no double free.
    # BaseStructType, to cover a user struct's identified type as well (#257).
    if (emitted_args
            and isinstance(params[0].type, ir.types.BaseStructType)
            and emitted_args[0].type == ir.PointerType(params[0].type)):
        emitted_args[0] = codegen.builder.load(emitted_args[0], name="self_by_value")

    casted = [codegen.utils.cast_for_param(v, p.type) for v, p in zip(emitted_args, params, strict=True)]
    result_value = codegen.builder.call(llvm_fn, casted)

    return codegen.utils.as_i1(result_value) if to_i1 else result_value


def _try_emit_external_call(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall]) -> ir.Value | None:
    """Emit a foreign (FFI) function call if `expr` is annotated with external_ref."""
    from sushi_lang.semantics.typesys import BuiltinType

    external_ref = getattr(expr, 'external_ref', None)
    if external_ref is None:
        return None

    llvm_fn = codegen.external_funcs.get(external_ref)
    sig = codegen.external_sigs.get(external_ref)
    if llvm_fn is None or sig is None:
        return None

    # Marshal the FIXED arguments. `string` args become char* (i8*) and are
    # registered for freeing at scope exit; everything else is passed through
    # with param casting against the declared parameter type.
    num_fixed = len(sig.param_types)
    emitted_args = []
    for arg, param_ty in zip(expr.args[:num_fixed], sig.param_types, strict=True):
        value = codegen.expressions.emit_expr(arg)
        if isinstance(param_ty, BuiltinType) and param_ty == BuiltinType.STRING:
            c_str = codegen.runtime.strings.emit_to_cstr(value)
            codegen.memory.register_cstr(c_str)
            emitted_args.append(c_str)
        else:
            emitted_args.append(value)

    params = list(llvm_fn.args)
    fixed_args = [codegen.utils.cast_for_param(v, p.type)
                  for v, p in zip(emitted_args, params, strict=True)]

    # Marshal the TRAILING variadic arguments. There is no declared target type,
    # so apply C default-argument promotion by hand against the emitted value's
    # LLVM type (and the inferred Sushi type for signedness): i8/i16 -> i32,
    # bool -> i32, f32 -> f64, string -> char*, ptr/i32/i64/f64 pass as-is.
    variadic_sushi_types = getattr(expr, 'variadic_arg_types', None) or []
    trailing_args = []
    for offset, arg in enumerate(expr.args[num_fixed:]):
        value = codegen.expressions.emit_expr(arg)
        sushi_ty = variadic_sushi_types[offset] if offset < len(variadic_sushi_types) else None
        trailing_args.append(
            _promote_variadic_arg(codegen, value, sushi_ty)
        )

    call_result = codegen.builder.call(llvm_fn, fixed_args + trailing_args)

    ret_ty = sig.ret_type
    if ret_ty is None or (isinstance(ret_ty, BuiltinType) and ret_ty == BuiltinType.BLANK):
        return ir.Constant(codegen.i32, 0)
    # `string` return: COPY the C char* into a fresh Sushi-owned buffer (#147). Sushi never
    # frees the foreign pointer; the owned copy is RAII-freed at scope exit (no leak).
    if isinstance(ret_ty, BuiltinType) and ret_ty == BuiltinType.STRING:
        return codegen.runtime.strings.emit_cstr_to_owned_fat_pointer(call_result)
    return call_result


def _promote_variadic_arg(codegen: 'LLVMCodegen', value: ir.Value, sushi_ty) -> ir.Value:
    """Apply C default-argument promotion to one untyped variadic argument."""
    from sushi_lang.semantics.typesys import BuiltinType

    # string -> char* (registered for the per-scope free, no leak).
    if isinstance(sushi_ty, BuiltinType) and sushi_ty == BuiltinType.STRING:
        c_str = codegen.runtime.strings.emit_to_cstr(value)
        codegen.memory.register_cstr(c_str)
        return c_str

    builder = codegen.builder
    vty = value.type

    if isinstance(vty, ir.FloatType):
        return builder.fpext(value, codegen.types.f64)

    # Narrow integers (i1/i8/i16) -> i32. bool is i1 in value position / i8 in
    # storage; either way normalize and widen. Signed Sushi types sign-extend,
    # unsigned (and bool) zero-extend.
    if isinstance(vty, ir.IntType) and vty.width < 32:
        unsigned = {
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.BOOL,
        }
        is_unsigned = isinstance(sushi_ty, BuiltinType) and sushi_ty in unsigned
        if is_unsigned:
            return builder.zext(value, codegen.i32)
        return builder.sext(value, codegen.i32)

    return value


def _check_stdlib_function_codegen(codegen: 'LLVMCodegen', function_name: str) -> tuple | None:
    """Check if a function is a stdlib function during code generation."""
    func_table = codegen.func_table

    possible_modules = ["time", "sys/env", "sys/process", "math", "random", "io/files"]

    for module_path in possible_modules:
        stdlib_func = func_table.lookup_stdlib_function(module_path, function_name)
        if stdlib_func is not None:
            return (module_path, stdlib_func)

    return None


def _emit_stdlib_function(codegen: 'LLVMCodegen', expr: Call, function_name: str,
                          module_and_func: tuple, to_i1: bool) -> ir.Value:
    """Emit code for a stdlib function call."""
    module_path, stdlib_func = module_and_func

    if module_path == "time":
        return emit_time_function(codegen, expr, function_name, to_i1)
    elif module_path == "sys/env":
        return emit_env_function(codegen, expr, function_name, to_i1)
    elif module_path == "sys/process":
        from sushi_lang.backend.expressions.calls.stdlib import emit_process_function
        return emit_process_function(codegen, expr, function_name, to_i1)
    elif module_path == "math":
        return emit_math_function(codegen, expr, function_name, to_i1)
    elif module_path == "random":
        from sushi_lang.backend.expressions.calls.stdlib import emit_random_function
        return emit_random_function(codegen, expr, function_name, to_i1)
    elif module_path == "io/files":
        from sushi_lang.backend.expressions.calls.stdlib import emit_files_function
        return emit_files_function(codegen, expr, function_name, to_i1)
    else:
        raise_internal_error("CE0055", name=f"{module_path}/{function_name}")
