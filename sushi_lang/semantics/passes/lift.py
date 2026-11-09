"""The lift pass (docs/design/ir.md 7.9, ruling Q8).

`LiftPass` runs on SHIR, between typecheck and borrow. It rewrites no node: the answers for
each `Lambda` of a body are rows of the `LiftResults` of the body, the symbol of the lifted
function and the captured locals in slot order. A body has its table checked (`check_lift`),
and goes on to the borrow pass, also a body whose walk in typecheck reported an error.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.body import Initializer, TemplateCheck
from sushi_lang.semantics.shir.lift_check import check_lift
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.slir.lower_signature import symbol_of
from sushi_lang.semantics.unit_symbols import declaring_unit

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.body import ShirBody
    from sushi_lang.semantics.shir.lowering import LoweredProgram
    from sushi_lang.semantics.tables import SymbolTables


class LiftPass:
    """The lift pass over the SHIR bodies of one unit, one body at a time."""

    def __init__(self, shir: LoweredProgram, *, next_stage: Callable[..., None],
                 unit: str) -> None:
        self.shir = shir
        self.next_stage = next_stage
        self.unit = unit

    def check_body(self, decl: object, body: ShirBody, *, tables: SymbolTables,
                   filename: Optional[str], typecheck_failed: bool = False) -> None:
        """Lift the SHIR body of `decl`, which the typecheck pass finished.

        The pass writes one row of each kind for each `Lambda`, a lambda in a lambda too, in
        the order of the walk. It checks the table of the body and gives the body, with the
        tables that typecheck read it in, to the borrow pass, with `typecheck_failed` when
        the walk of typecheck reported an error. The symbol has the symbol of the body as its
        prefix, so a lambda of an instance is one function for each instance (ir.md 8.2).
        """
        table = self.shir.lift_table_of(body)
        lambdas = [value for _path, value in walk(body.block, "block")
                   if isinstance(value, e.Lambda)]
        if lambdas:
            prefix = self._prefix(decl, body, tables)
            for index, lam in enumerate(lambdas):
                table.record_symbol(lam.shir_id, f"{prefix}.__lambda_{index}")
                table.record_environment(lam.shir_id, lam.captures)
        check_lift(body, table, self.shir.decls)
        if typecheck_failed:
            self.next_stage(decl, body, tables=tables, filename=filename,
                            typecheck_failed=True)
            return
        self.next_stage(decl, body, tables=tables, filename=filename)

    def _prefix(self, decl: object, body: ShirBody, tables: SymbolTables) -> str:
        """The symbol of the body. A template check and an initializer never lower, so their
        name is enough."""
        if isinstance(body.origin, (TemplateCheck, Initializer)):
            return self.shir.decls.name_of(body.fn_id)
        return symbol_of(decl, declaring_unit(decl, self.unit), body, tables.perk_impls)
