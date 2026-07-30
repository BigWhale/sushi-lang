"""
Name (variable reference) emission for the Sushi language compiler.

This module owns name resolution for the backend. Two halves:

  resolve_name_slot          -- the ADDRESS of a name (its alloca, or the global
                                backing a constant)
  emit_name                  -- the VALUE of a name (a load of that address, or a
                                math constant / function reference, which have no
                                address of their own)

Keeping both here is what fixes #248. The value half always knew about global
constants; every path that wanted an ADDRESS went straight to the local-alloca table
instead, so `let i32[3] c = PRIMES` worked while `PRIMES[0]`, `PRIMES.len()` and
`PRIMES.iter()` were all `CE0000: KeyError: 'undefined name: PRIMES'`. One resolver,
consulted by both halves, cannot drift like that again.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import Name
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def resolve_name_slot(codegen: 'LLVMCodegen', name: str) -> Optional[ir.Value]:
    """Return the address of `name`: its local alloca, else its global constant.

    Locals are consulted FIRST -- a local shadows a same-named constant. That ordering
    is not cosmetic: `emit_name` used to check constants first, so with a
    `const i32[3] PRIMES` in scope a local `let i32[4] PRIMES = [7, 8, 9, 10]` read the
    CONSTANT (`CE0017: cannot convert '[3 x i32]' to '[4 x i32]'`), while `PRIMES[0]`
    -- which only ever consulted the local table -- correctly read the local. The two
    halves disagreed about what the same name meant.

    A constant's global is an `ir.GlobalVariable`, which is already a pointer whose
    `.type.pointee` is the value type, so callers that GEP or bounds-check through the
    returned address need no special case for it.

    Args:
        codegen: The LLVM codegen instance.
        name: The name to resolve.

    Returns:
        A pointer to the name's storage, or None if it names neither a local nor a
        constant (a function reference, or a genuine unknown).
    """
    slot = codegen.memory.try_find_local_slot(name)
    if slot is not None:
        return slot
    return codegen.constants.get(name)


def resolve_name_semantic_type(codegen: 'LLVMCodegen', name: str) -> Optional['Type']:
    """Return the Sushi type of `name`, whether it is a local or a constant.

    Mirrors resolve_name_slot's local-first ordering, over all three tables that record
    a name's type:

      memory.find_semantic_type  -- scope-aware, so it resolves shadowing correctly
      codegen.variable_types     -- a flat parallel map (lets, params, foreach/match
                                    bindings); consulted second because it cannot
                                    distinguish two same-named locals in sibling scopes
      const_table.by_name        -- constants, which appear in NEITHER of the above,
                                    because nothing ever declared them as locals

    That last gap is why `PRIMES.iter()` and `PRIMES.hash()` still failed (CE0041 /
    CE0056) after the address half of #248 was fixed: both need the receiver's element
    type, and both asked a locals-only table for it.

    Args:
        codegen: The LLVM codegen instance.
        name: The name to resolve.

    Returns:
        The semantic type, or None if unknown.
    """
    semantic_ty = codegen.memory.find_semantic_type(name)
    if semantic_ty is not None:
        return semantic_ty
    semantic_ty = codegen.variable_types.get(name)
    if semantic_ty is not None:
        return semantic_ty
    const_sig = codegen.const_table.by_name.get(name)
    return const_sig.const_type if const_sig is not None else None


def emit_name(codegen: 'LLVMCodegen', expr: Name, to_i1: bool) -> ir.Value:
    """Emit variable or constant reference.

    Resolves through resolve_name_slot (locals, then global constants -- including
    string constants, stored as fat-pointer structs). Math constants and bare function
    references have no storage of their own and are handled separately.
    For reference parameters, automatically dereferences them when used as values.

    Args:
        codegen: The LLVM codegen instance.
        expr: The name expression.
        to_i1: Whether to convert result to i1.

    Returns:
        The loaded variable or constant value.
    """
    # Check for math module constants (PI, E, TAU)
    if expr.id in {'PI', 'E', 'TAU'}:
        from sushi_lang.sushi_stdlib.src import math as math_module
        if math_module.is_builtin_math_constant(expr.id):
            _, value = math_module.get_builtin_math_constant_value(expr.id)
            f64_value = ir.Constant(ir.DoubleType(), value)
            return codegen.utils.as_i1(f64_value) if to_i1 else f64_value

    slot = resolve_name_slot(codegen, expr.id)
    if slot is not None:
        # load_with_reference_handling dereferences a &peek/&poke parameter, whose slot
        # holds a pointer to the borrowed variable rather than the value itself. A
        # constant is never a reference parameter, so it takes the plain-load path.
        from sushi_lang.backend.expressions import type_utils
        v = type_utils.load_with_reference_handling(codegen, expr.id, slot)
        return codegen.utils.as_i1(v) if to_i1 else v

    # Neither a local nor a constant: a bare reference to a top-level function is a
    # first-class function value -> a non-capturing fat pointer {thunk, null, null}.
    # The thunk bridges the bare fn into the uniform env-passing indirect ABI.
    llvm_fn = codegen.funcs.get(expr.id)
    if llvm_fn is not None:
        from sushi_lang.backend.runtime import closures
        return closures.materialize_function_ref(codegen, llvm_fn)
    # Variable/constant not found (should be caught in semantic analysis)
    raise_internal_error("CE0055", name=expr.id)

