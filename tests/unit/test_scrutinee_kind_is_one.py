"""A reference binder reads its scrutinee through ONE decision (#783).

`_bind_payload_ref` took two booleans, `require_named_scrutinee` and `owns_scrutinee`,
for three behaviours; the fourth combination had no meaning and nothing refused it. On
SHIR the decision is one predicate, `pattern_takes.owns_scrutinee`, which the `nom` binders
read too, and the reference binders ask it in `references.declare_reference_binders`. This
gate keeps a flag pair from coming back.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics.passes.borrow import bindings, references


def test_the_reference_binders_ask_the_one_predicate():
    assert "owns_scrutinee(" in inspect.getsource(references.declare_reference_binders)


def test_no_flag_pair_survives_in_the_modules():
    for module in (bindings, references):
        source = inspect.getsource(module)
        assert "require_named_scrutinee" not in source
        assert "inside_own" not in source
