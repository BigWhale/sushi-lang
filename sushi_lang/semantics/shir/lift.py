"""The answers of the `lift` pass for one body (docs/design/ir.md 7.9, ruling Q8).

`lift` rewrites no SHIR node: the `Lambda` stays in the tree, and SLIR lowering cuts the
function and builds the closure from this table. Each answer is a row of the `LiftResults`
of the body, by the `shir_id` of the `Lambda`: the symbol of the lifted function, and the
environment layout, the captured locals in slot order. The mode of a capture is an answer of
`borrow` (`OwnershipResults`), not of this table. Lowering lists each body with its table
(`LoweredProgram.lift_table_of`), and the lift pass is the one writer (gate:
`tests/unit/test_lift_results_have_one_writer.py`). A key has one answer: a second write is
an internal error. When the pass ends, `lift_check.py` finds each `Lambda` with no symbol or
no environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.side_table import SideTable, row_table, write_once


@dataclass(slots=True)
class LiftResults(SideTable):
    """The answers of `lift` for one body, one row kind for each decision of ir.md 7.9."""
    _symbols: dict[ShirId, str] = row_table()
    _environments: dict[ShirId, tuple[LocalId, ...]] = row_table()

    def symbol_of(self, lam: ShirId) -> Optional[str]:
        """The symbol of the function that `lift` cuts from the lambda `lam`, or None."""
        return self._symbols.get(lam)

    def environment_of(self, lam: ShirId) -> Optional[tuple[LocalId, ...]]:
        """The captured locals of the lambda `lam`, in slot order, or None."""
        return self._environments.get(lam)

    def record_symbol(self, lam: ShirId, symbol: str) -> None:
        write_once(self._symbols, lam, symbol, "lift", "symbol")

    def record_environment(self, lam: ShirId, captures: tuple[LocalId, ...]) -> None:
        write_once(self._environments, lam, captures, "lift", "environment")
