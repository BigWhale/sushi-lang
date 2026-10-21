"""The prototype of a `SlirFunction` reads the interned type of its return slot.

The port of `test_spelled_result_return_is_interned.py` (#857) to the SLIR emitter. SLIR
lowering gives the return slot `_0` the interned `Result@(T, E)` of a channel or of a
spelled `Result` return through the one derivation, `channel_result`
(`semantics/channel.py`). The emitter checks the LLVM function that the declaration round
made against the type of `_0`, and it has no reader of its own for a spelled `Result`:
no `resolved_result`, no `channel_result_of`, no `GenericTypeRef`. The fixture
`tests/ir/slir/spelled_result/` shows the interned slot of a spelled return. The old gate
stays: the declaration round of the backend still reads the stamp (ruling E1).
"""
from __future__ import annotations

import inspect
from pathlib import Path

from sushi_lang.backend.slir import emit
from sushi_lang.semantics.slir import lower

SLIR_BACKEND = Path(emit.__file__).parent
SPELLED_RESULT_READERS = ("resolved_result", "channel_result_of", "GenericTypeRef",
                          "declared_return")


def test_the_emitter_has_no_spelled_result_reader():
    hits = [f"{path.name}: {word}"
            for path in sorted(SLIR_BACKEND.glob("*.py"))
            for word in SPELLED_RESULT_READERS
            if word in path.read_text(encoding="utf-8")]
    assert not hits, hits


def test_the_prototype_check_reads_the_return_slot():
    source = inspect.getsource(emit._open)
    assert "function.locals[0]" in source
    assert "return_type" in source


def test_lowering_types_the_return_slot_through_the_one_derivation():
    source = inspect.getsource(lower._return_type)
    assert "channel_result(" in source
    assert "resolved_result" not in source
