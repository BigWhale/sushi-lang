"""Every read-only receiver kind has one code, and the checker's table holds each one."""
from __future__ import annotations

from sushi_lang.semantics.passes.borrow import READONLY_RECEIVERS

KIND_CODES = {
    "peek_reference": "CE2408",
    "pattern_binding": "CE2414",
    "method_receiver": "CE2421",
    "method_parameter": "CE2422",
    "function_parameter": "CE2422",
    "let_borrow": "CE2426",
    "unbound_chained": "CE2429",
}


def test_every_readonly_kind_is_in_the_gate_table():
    """A kind in the checker's table without a row here is a hole in this matrix."""
    table_codes = {kind.code.code for kind in READONLY_RECEIVERS}
    # CE2429 is the shape-keyed sixth kind (#352): it answers BEFORE the state table,
    # so it is deliberately not a table row.
    assert table_codes == set(KIND_CODES.values()) - {"CE2429"}
