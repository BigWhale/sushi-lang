"""The `ptr` quarantine of the SLIR verifier (docs/design/ir.md 8.13, S25).

No `ptr`, and no type that holds one (`Maybe@(ptr)`, `Result@(ptr, E)`), is a parameter
or the return of a public function or a public extension method. `public_signatures.py` is
the language rule (CE5002, CE5008); the verifier only checks it again. A C function has no
SLIR body, so the rule never reads one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, NoReturn, Optional

from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.slir.function import SlirFunction
from sushi_lang.semantics.slir.verify_calls import extension_of, function_of
from sushi_lang.semantics.slir.verify_rules import PTR_QUARANTINE
from sushi_lang.semantics.type_predicates import contains_foreign_ptr

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import ExtensionTable, FunctionTable


def is_public_function(funcs: FunctionTable, extensions: ExtensionTable,
                       function: SlirFunction) -> bool:
    """Whether the declaration of `function`, found by its symbol, is `public`."""
    sig = function_of(funcs, function.symbol, function.name)
    if sig is not None:
        return sig.is_public
    _, dot, method = function.name.rpartition(".")
    record = extension_of(extensions, function.symbol, method) if dot else None
    return record is not None and record.is_public


def check_ptr_quarantine(function: SlirFunction,
                         fail: Callable[[str, str, Optional[Span]], NoReturn]) -> None:
    """The quarantine rule over a public function: the return slot and each parameter."""
    slots = [(function.locals[0], "the return")] + [
        (function.locals[param.index], f"the parameter _{param.index}")
        for param, _ in function.params]
    for local, what in slots:
        if contains_foreign_ptr(local.ty):
            fail(PTR_QUARANTINE, f"{what} of the public function has the type "
                                 f"{display_type(local.ty)}", local.loc)
