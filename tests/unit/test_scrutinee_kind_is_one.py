"""A payload binding reads its scrutinee through ONE `ScrutineeKind` (#783).

`_bind_payload_ref` took two booleans, `require_named_scrutinee` and `owns_scrutinee`,
for three behaviours; the fourth combination had no meaning and nothing refused it.
The enum names the three, and this gate keeps the pair from coming back.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics.passes.borrow import bindings


def test_the_kind_names_three_behaviours():
    assert [k.name for k in bindings.ScrutineeKind] == [
        "BORROWED", "OWNED", "OWN_PAYLOAD"]


def test_the_payload_reference_takes_the_kind_and_no_flag():
    params = inspect.signature(bindings._bind_payload_ref).parameters
    assert "kind" in params
    assert "require_named_scrutinee" not in params
    assert "owns_scrutinee" not in params


def test_no_flag_pair_survives_in_the_module():
    source = inspect.getsource(bindings)
    assert "require_named_scrutinee" not in source
    assert "inside_own" not in source
