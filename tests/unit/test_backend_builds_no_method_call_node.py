"""The backend hands the incoming call node to an emitter and builds no copy of it.

A `MethodCall` rebuilt from the incoming node carries no typecheck stamp, so the
first emitter that reads one (for example `callee_param_modes`) reads `None`. The
emitters read `.receiver`, `.method`, `.args` and `.loc`, which a `DotCall` has too,
so the node that arrives is the node that goes down.

The two container emitters (`HashMap@(K, V)`, `List@(T)`) are one body with one row
per container.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from sushi_lang.backend.expressions.calls import generics

BACKEND = Path(generics.__file__).resolve().parents[2]


def test_no_backend_module_rebuilds_a_method_call_from_its_node():
    hits = []
    for path in sorted(BACKEND.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "MethodCall(receiver=" in line:
                hits.append(f"{path.relative_to(BACKEND)}:{lineno}")
    assert hits == [], hits


def test_no_backend_module_writes_a_stamp_onto_a_call_node():
    source = inspect.getsource(generics)
    assert ".resolved_enum_type = " not in source


def test_the_two_container_emitters_share_one_body():
    assert hasattr(generics, "_try_emit_container_method")
    for name in ("try_emit_hashmap_method", "try_emit_list_method"):
        body = inspect.getsource(getattr(generics, name))
        assert "_try_emit_container_method" in body, name
        assert "infer_semantic_type" not in body, name
        assert "emit_receiver_as_pointer" not in body, name
