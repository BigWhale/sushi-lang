"""The lifted closure environment is a `poke` borrow, not a `peek` one.

Lambda lifting turns a closure into a top-level function whose first parameter is the
heap environment, and rewrites every captured read into a field access on it. So a
capture mutation -- `xs.push(x)`, legal by design since T1.5, because the environment
OWNS a move-captured `List@(T)` -- becomes `__closure_env.xs.push(x)`, a write THROUGH
that parameter.

It was declared `peek` until the `peek` write rule became total (FIX.md R1). Nothing
enforced read-only before that, so the untruthful mode had no consequence; once it was
enforced, it turned two legal shapes into CE2408. The declaration was corrected rather
than the rule carved out, because the environment is the closure's own storage and not a
borrow of any caller's value.

This test pins the DECLARATION, where the behavioural tests
(`tests/closures/test_closure_list_mutate.sushi` for the mutating method,
`tests/closures/test_closure_env_poke_borrow.sushi` for the `poke` borrow of a capture)
pin the consequence. Both halves are worth having: the mode is invisible in codegen -- no
backend code reads `ReferenceType.mutability` -- so nothing but a semantics rule can ever
catch a silent flip back.

**If a future rule needs an exemption for this parameter rather than an accurate
declaration, that is the signal to give it its own kind** (a `synthesized` flag, or a
distinct param kind) so a rule can ask "is this a user borrow?" instead of matching the
name `__closure_env`. See docs/design/closures.md, "Lambda lowering".
"""
from __future__ import annotations

from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.passes.lambda_lift import ENV_PARAM_NAME
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType


_SRC = """\
fn run() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    let fn(i32) -> i32 f = |i32 x|:
        xs.push(x)
        return Result.Ok(xs.len())
    let i32 a = f(5)??
    return Result.Ok(a)

fn main() i32:
    println(run().realise(-1))
    return Result.Ok(0)
"""


def _lifted_functions(program) -> list[FuncDef]:
    """Every synthesized function carrying the environment parameter."""
    return [fn for fn in program.functions
            if fn.params and fn.params[0].name == ENV_PARAM_NAME]


def test_lifted_env_param_is_a_poke_reference(analyze_program):
    analysis = analyze_program(_SRC)
    lifted = _lifted_functions(analysis.program)

    assert lifted, "the capturing lambda was not lifted, so this test proves nothing"

    for fn in lifted:
        env_param = fn.params[0]
        assert isinstance(env_param.ty, ReferenceType), (
            f"{fn.name}: the environment parameter must stay a borrow -- the closure "
            f"VALUE owns the environment and frees it through drop_ptr"
        )
        assert env_param.ty.mutability is BorrowMode.POKE, (
            f"{fn.name}: the environment parameter is &{env_param.ty.mutability}, but the "
            f"lifted body writes through it whenever the closure mutates a capture. "
            f"peek makes that a CE2408"
        )


def test_a_capture_mutation_is_rewritten_through_the_env_param(analyze_program):
    """The reason the mode matters: the write really does go through that parameter."""
    analysis = analyze_program(_SRC)
    reporter = analysis.reporter

    assert "CE2408" not in [item.code for item in reporter.items], (
        "mutating a captured value is legal (T1.5); a CE2408 here means the environment "
        "parameter is declared read-only again"
    )
