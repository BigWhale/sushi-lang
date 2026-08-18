"""Pass 1.5 reads every extension declaration, whichever list it is filed under (#389).

The AST builder splits extensions two ways: a plain target goes to `program.extensions`, and
a target spelled `@(...)` goes to `program.generic_extensions` -- CONCRETE arguments
included, so `extend List@(i32)` is in the second list beside the `extend Box@(T)`
templates. Pass 1.5 walked the first list only, so a generic type named in such a
declaration was never collected and the declaration reported a false CE2001, for a type the
program declares.

The gate reads the ENUM AND STRUCT TABLES after analysis rather than the diagnostic: a
collected instantiation is what puts the type there, and asking the table says which half
failed when it fails.
"""
from __future__ import annotations

import pytest

_PRELUDE = """\
struct Box@(T):
    T value

struct Pair@(A, B):
    A first
    B second

enum Wrap@(T):
    Full(T)
    Empty
"""

_TAIL = """\
fn main() i32:
    let Box@(i32) b = Box(1)
    return Result.Ok(0)
"""

# (id, declaration, the interned name the declaration must make exist)
_CASES = [
    (
        "return_builtin_enum",
        "extend Box@(i32) tagged() Maybe@(i32):\n    return Maybe.Some(self.value)\n",
        "Maybe<i32>",
    ),
    (
        "return_user_enum",
        "extend Box@(i32) wrapped() Wrap@(i32):\n    return Wrap.Full(self.value)\n",
        "Wrap<i32>",
    ),
    (
        "parameter_builtin_enum",
        "extend Box@(i32) plus(Maybe@(i32) m) i32:\n    return self.value + m.realise(0)\n",
        "Maybe<i32>",
    ),
    (
        "parameter_user_struct",
        "extend Box@(i32) paired(Pair@(i32, i32) p) i32:\n"
        "    return self.value + p.first + p.second\n",
        "Pair<i32, i32>",
    ),
    (
        "owning_signature_type",
        'extend Box@(i32) named() Maybe@(string):\n    return Maybe.Some("marvin")\n',
        "Maybe<string>",
    ),
    (
        "body_annotation",
        "extend Box@(i32) counted() i32:\n"
        "    let List@(i32) out = List.new()\n"
        "    let i32 n = out.len()\n"
        "    out.destroy()\n"
        "    return n\n",
        "List<i32>",
    ),
]


def _interned_names(analysis) -> set[str]:
    """Every struct and enum name the analysis interned."""
    analyzer = analysis.analyzer
    assert analyzer is not None, "analysis produced no analyzer"
    return set(analyzer.enums.by_name) | set(analyzer.structs.by_name)


@pytest.mark.parametrize("case_id,declaration,interned", _CASES,
                         ids=[c[0] for c in _CASES])
def test_a_generic_target_declaration_interns_the_types_it_names(
        analyze_program, case_id, declaration, interned):
    """A type named in a generic-target extension reaches the tables."""
    analysis = analyze_program(_PRELUDE + declaration + _TAIL, name=case_id)

    assert interned in _interned_names(analysis), (
        f"{case_id}: '{interned}' is named by the declaration and was never interned, so "
        "Pass 1.5 did not read it. The declaration reports a false CE2001."
    )
    assert not analysis.reporter.has_errors, (
        f"{case_id}: semantic analysis reported an error:\n"
        + "\n".join(str(d) for d in analysis.reporter.diagnostics)
    )


def test_a_template_target_is_left_alone():
    """A TEMPLATE target is out of scope, and stays out until its body is per-instantiation.

    Every instantiation of a generic-target extension shares ONE body AST, so Pass 2's and
    Pass 3's per-instantiation stamps land on the same nodes. Reading a template's signature
    here would make that reachable -- an invalid-IR CE0000 in place of the clean error the
    program gets today. This asserts the restriction on purpose: delete it in the PR that
    gives each instantiation its own body (#390), not before.
    """
    from sushi_lang.semantics.generics.types import (
        GenericTypeRef, TypeParameter, substitute_type_params,
    )
    from sushi_lang.semantics.typesys import BuiltinType, UnknownType

    # The spelling a type parameter has inside a generic type in a DECLARED position.
    nested = GenericTypeRef(base_name="Maybe", type_args=(UnknownType("T"),))
    assert substitute_type_params(nested, {"T": BuiltinType.I32}) == nested

    # A top-level TypeParameter does substitute -- that is what a template's bare `T`
    # return type uses, and it has always worked.
    assert substitute_type_params(TypeParameter(name="T"), {"T": BuiltinType.I32}) \
        == BuiltinType.I32
