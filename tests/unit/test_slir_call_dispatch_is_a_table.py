"""The SLIR `Call` emitter dispatches over the closed callee set through one table.

The port of `test_method_call_dispatch_is_a_table.py` (docs/design/ir.md 7.6, 8.6): SLIR
lowering already chose the callee kind, so the emitter does not decide it again. Each
variant of `SlirCallee` has one row of `CALL_EMITTERS`, a kind that the emitter does not
handle yet included (its row stops the compile). Every row takes one shape, the state and
the statement. The one tail that emits the `call` instruction is written once. The old
gate stays until Gate 4 with the old handler chains.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_args

from sushi_lang.backend.slir import emit
from sushi_lang.semantics.slir.statements import SlirCallee

SLIR_BACKEND = Path(emit.__file__).parent


def test_every_callee_variant_has_one_row():
    assert set(emit.CALL_EMITTERS) == set(get_args(SlirCallee))


def test_every_row_takes_the_state_and_the_statement():
    for kind, handler in emit.CALL_EMITTERS.items():
        assert list(inspect.signature(handler).parameters) == ["state", "statement"], kind


def test_the_call_statement_asks_the_table_and_holds_no_callee_arm():
    tree = ast.parse(inspect.getsource(emit._call).lstrip())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "CALL_EMITTERS" in names
    arms = [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance"]
    assert not arms, f"the call statement decides the callee kind again at line(s) {arms}"


def test_the_call_instruction_is_emitted_once():
    hits = [f"{path.name}:{lineno}"
            for path in sorted(SLIR_BACKEND.glob("*.py"))
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "builder.call(" in line]
    assert len(hits) == 1, hits
