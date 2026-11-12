"""The completeness check of `LiftResults` (docs/design/ir.md 7.9, ruling Q8).

When the `lift` pass ends on a body with no error, each `Lambda` of the body, a lambda in
the body of a lambda too, has a symbol and an environment layout. The check walks the body
once (`walk.py`), and a gap is the internal error CE0157, which names the pass and the
table.
"""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.lift import LiftResults
from sushi_lang.semantics.shir.side_table import MissingAnswer, raise_first_gap
from sushi_lang.semantics.shir.walk import walk


def missing_lift(body: ShirBody, lift: LiftResults) -> list[MissingAnswer]:
    """Each `Lambda` of `body` with no symbol or no environment, in the order of the walk."""
    gaps: list[MissingAnswer] = []
    for _path, value in walk(body.block, "block"):
        if not isinstance(value, e.Lambda):
            continue
        if lift.symbol_of(value.shir_id) is None:
            gaps.append(MissingAnswer("symbol", "Lambda", value.loc))
        if lift.environment_of(value.shir_id) is None:
            gaps.append(MissingAnswer("environment", "Lambda", value.loc))
    return gaps


def check_lift(body: ShirBody, lift: LiftResults, decls: DeclNames) -> None:
    """CE0157 at the first `Lambda` of `body` that `lift` did not answer for."""
    raise_first_gap(er.ERR.CE0157, missing_lift(body, lift), decls.name_of(body.fn_id),
                    writer="lift", table=type(lift).__name__)
