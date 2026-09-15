"""The lift pass names a CAPABILITY, not a private method of another pass (#687).

`LambdaLifter` took `TypeValidator._validate_function` as its hook. That made the lift
pass impossible to run or to test without a `TypeValidator`, and it meant a rename of a
private method in the typecheck pass broke a different pass with no diagnostic. The
`Annotator` protocol states the one thing the lifter needs; the typecheck pass answers
it with a PUBLIC method.

The second half is the registration guard. It raised a bare `RuntimeError`, which
reached the user as CE0000 with no explanation of what had gone wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.passes import lift as lift_module
from sushi_lang.semantics.passes.collect.functions import FunctionTable
from sushi_lang.semantics.passes.collect.structs import StructTable
from sushi_lang.semantics.passes.lift import Annotator, LambdaLifter
from sushi_lang.semantics.passes.types import TypeValidator

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"


def _lifter(annotate=None) -> LambdaLifter:
    """A lifter over empty REAL tables, which is all the pass needs for one literal."""
    program = a.Program(None, [], [], [], [], [], [], [], [], [], [], [])
    return LambdaLifter(StructTable(), FunctionTable(), program, annotate=annotate)


def _lambda() -> a.Lambda:
    return a.Lambda(None, [], a.IntLit(None, 0))


# --- the annotator contract --------------------------------------------------------


def test_the_typecheck_pass_answers_the_annotator_contract():
    """The provider satisfies the protocol the consumer declares."""
    assert isinstance(TypeValidator, type)
    assert issubclass(TypeValidator, Annotator) or hasattr(
        TypeValidator, "annotate_function")


def test_annotate_function_is_public_and_takes_one_function():
    method = TypeValidator.annotate_function
    assert not method.__name__.startswith("_")
    assert method.__doc__, "the contract needs to say why it is public"


def test_the_lifter_calls_the_contract_and_not_a_private_name():
    """A stand-in annotator proves the lifter goes through `annotate_function`."""
    seen: list[str] = []

    class Stub:
        def annotate_function(self, func: a.FuncDef) -> None:
            seen.append(func.name)

    stub = Stub()
    assert isinstance(stub, Annotator)
    lifter = _lifter(annotate=stub)
    lifter._lift(_lambda())
    assert seen == ["__lambda_0"]


def test_the_lifter_runs_without_an_annotator():
    """The hook is optional: the extension path builds a lifter with none."""
    lifter = _lifter()
    lifter._lift(_lambda())
    assert lifter._lifted[0].name == "__lambda_0"


def test_no_pass_reaches_into_the_typecheck_pass_for_a_private_validator():
    """The fault this seam ends, pinned at the source it was written in."""
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.parts[-3:-1] == ("passes", "types"):
            continue  # the pass may name its own privates
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"annotate\s*=\s*\w+\._", line):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        f"a pass takes a private method of another pass as a hook: {offenders}"
    )


# --- the registration guard --------------------------------------------------------


def test_a_registration_clash_raises_a_registered_diagnostic(monkeypatch):
    """CE0137, not a bare RuntimeError that reaches the user as CE0000."""
    monkeypatch.setattr(
        "sushi_lang.semantics.generics.synthesis.register_synthesized_function",
        lambda *args, **kwargs: False)

    lifter = _lifter()
    with pytest.raises(InternalCompilerError) as caught:
        lifter._lift(_lambda())
    assert caught.value.code == "CE0137"
    assert caught.value.params["name"] == "__lambda_0"


def test_the_guard_is_not_a_bare_exception_any_more():
    source = (SOURCE_ROOT / "semantics/passes/lift.py").read_text(encoding="utf-8")
    assert "raise RuntimeError" not in source
    assert 'raise_internal_error("CE0137"' in source


# --- the split ---------------------------------------------------------------------


def test_lift_delegates_each_job_it_used_to_inline():
    """`_lift` reads as its jobs, so a reader finds one of them without the others."""
    for name in ("_claim_index", "_synthesize_env_struct", "_register",
                 "_annotate_then_walk"):
        assert callable(getattr(LambdaLifter, name)), name
    for name in ("_normalized_body", "_build_lifted_function", "_rewrite_captures"):
        assert callable(getattr(lift_module, name)), name


def test_claim_index_steps_past_a_name_another_unit_took():
    """The #402 rule: a taken index in EITHER table is skipped."""
    lifter = _lifter()
    lifter.func_table.by_name["__lambda_0"] = object()
    lifter.structs.by_name["__closure_env_1"] = object()
    assert lifter._claim_index() == 2


def test_an_expression_body_is_normalized_into_a_returning_block():
    body = lift_module._normalized_body(_lambda())
    assert isinstance(body, a.Block)
    assert isinstance(body.statements[0], a.Return)
