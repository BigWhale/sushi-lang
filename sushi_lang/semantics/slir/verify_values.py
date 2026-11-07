"""The verifier rules of a function value (docs/design/ir.md 8.6, 8.13).

Each `Convention` of an `Indirect` call matches the parameter modes of the `FunctionType` of
its operand, because the modes are part of the type. A function value is the one owning value
beside a `string` that may come as a copy: a named function owns no heap, and `borrow` adopts
it (`copies_with_no_transfer`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.param_modes import CalleeKind, effective_modes
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.verify_rules import CONVENTIONS
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check


def copies_with_no_transfer(ty: Optional[Type]) -> bool:
    """A value of this type can own nothing while its type says that it owns: a `string` from a
    literal, and a function value of a named function."""
    return ty == BuiltinType.STRING or isinstance(ty, FunctionType)


def indirect_conventions(check: Check, stmt: st.Call, callee: st.Indirect) -> None:
    ty = check.operand_type(callee.operand, stmt.span)
    called = None if ty is None else deref_type(ty)
    if not isinstance(called, FunctionType):
        check.fail(CONVENTIONS, f"the operand of an indirect call has the type {ty}, which is "
                                f"no function type", stmt.span)
    wanted = tuple(mode.convention
                   for mode in effective_modes(called.modes, CalleeKind.INDIRECT))
    given = tuple(convention for _, convention in stmt.args)
    if len(given) != len(wanted):
        check.fail(CONVENTIONS, f"the indirect call has {len(given)} arguments, and its function "
                                f"type declares {len(wanted)}", stmt.span)
    for index, (have, want) in enumerate(zip(given, wanted, strict=True)):
        if have is not want:
            check.fail(CONVENTIONS, f"argument {index} of the indirect call is @{have.value}, "
                                    f"and its parameter is @{want.value}", stmt.span)
