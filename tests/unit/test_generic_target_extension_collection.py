"""The instantiate pass reads every extension declaration, whichever list it is filed under (#389).

The AST builder splits extensions two ways: a plain target goes to `program.extensions`, and
a target spelled `@(...)` goes to `program.generic_extensions` -- CONCRETE arguments
included, so `extend List@(i32)` is in the second list beside the `extend Box@(T)`
templates. The instantiate pass walked the first list only, so a generic type named in such a
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
        "the instantiate pass did not read it. The declaration reports a false CE2001."
    )
    assert not analysis.reporter.has_errors, (
        f"{case_id}: semantic analysis reported an error:\n"
        + "\n".join(str(d) for d in analysis.reporter.diagnostics)
    )


# A TEMPLATE target reads its signature per instantiation of the target. This case used to
# assert the OPPOSITE -- the restriction that kept a template's signature unread, because
# every instantiation shared one body AST and the per-instantiation stamps collided on it.
# Each instantiation owns its body now (#391), so the restriction is gone and the positive
# case takes its place.

_TEMPLATE_CASES = [
    ("template_return", "extend Box@(T) peeked() Maybe@(T):\n"
                        "    return Maybe.Some(self.value.clone())\n",
     ["Maybe<i32>", "Maybe<string>"]),
    ("template_parameter", "extend Box@(T) plus(Maybe@(T) m) T:\n"
                           "    return m.realise(self.value.clone())\n",
     ["Maybe<i32>", "Maybe<string>"]),
    ("template_nested", "extend Box@(T) wrapped() Maybe@(List@(T)):\n"
                        "    return Maybe.Some(List.new())\n",
     ["List<i32>", "List<string>", "Maybe<List<i32>>", "Maybe<List<string>>"]),
]

_TWO_INSTANTIATIONS = """\
fn main() i32:
    let Box@(i32) a = Box(1)
    let Box@(string) b = Box("marvin")
    return Result.Ok(0)
"""


@pytest.mark.parametrize("case_id,declaration,interned", _TEMPLATE_CASES,
                         ids=[c[0] for c in _TEMPLATE_CASES])
def test_a_template_target_interns_one_signature_per_instantiation(
        analyze_program, case_id, declaration, interned):
    """Both instantiations of a template intern their own signature types."""
    analysis = analyze_program(_PRELUDE + declaration + _TWO_INSTANTIATIONS, name=case_id)

    names = _interned_names(analysis)
    missing = [name for name in interned if name not in names]
    assert not missing, (
        f"{case_id}: {missing} never interned. A template's signature is read once per "
        "instantiation of its target."
    )
    assert not analysis.reporter.has_errors, (
        f"{case_id}: semantic analysis reported an error:\n"
        + "\n".join(str(d) for d in analysis.reporter.diagnostics)
    )


# -- the array-target classifier (ruling 3 of the UFCS epic) ----------------------------
#
# `extend T[]` and `extend Crate[]` are spelled the same way -- a bare name in the
# element position -- and the collect pass tells them apart with the same declared-name
# question the `@(...)` classifier answers. A template files under the synthetic
# `$array` base key; a declared name stays a concrete extension.

def test_array_element_naming_nothing_binds_a_type_parameter():
    from sushi_lang.semantics.generics.extension_targets import (
        ARRAY_BASE_KEY, classify_array_extension_target)
    from sushi_lang.semantics.typesys import UnknownType

    shape = classify_array_extension_target(UnknownType(name="T"), lambda name: False)
    assert shape is not None
    assert shape.base_name == ARRAY_BASE_KEY
    assert shape.param_names == ("T",)
    assert not shape.is_concrete


def test_array_element_naming_a_declared_type_is_concrete():
    from sushi_lang.semantics.generics.extension_targets import (
        classify_array_extension_target)
    from sushi_lang.semantics.typesys import BuiltinType, UnknownType

    declared = classify_array_extension_target(UnknownType(name="Crate"),
                                               lambda name: name == "Crate")
    assert declared is not None and declared.is_concrete

    builtin = classify_array_extension_target(BuiltinType.I32, lambda name: False)
    assert builtin is not None and builtin.is_concrete


def test_array_element_of_any_other_shape_is_invalid():
    from sushi_lang.semantics.generics.extension_targets import (
        classify_array_extension_target)
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import DynamicArrayType, UnknownType

    generic = GenericTypeRef(base_name="Maybe", type_args=(UnknownType(name="T"),))
    assert classify_array_extension_target(generic, lambda name: True) is None

    nested = DynamicArrayType(base_type=UnknownType(name="T"))
    assert classify_array_extension_target(nested, lambda name: False) is None


def test_an_array_template_files_under_the_synthetic_base_key(analyze_program):
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY

    analysis = analyze_program(
        "extend T[] count_plus_one() i32:\n"
        "    return self.len() + 1\n"
        "\n"
        "fn main() i32:\n"
        "    let i32[] xs = from([1, 2, 3])\n"
        "    println(\"{xs.count_plus_one()}\")\n"
        "    return Result.Ok(0)\n",
        name="array_template_key")

    assert not analysis.reporter.has_errors, (
        "semantic analysis reported an error:\n"
        + "\n".join(str(d) for d in analysis.reporter.diagnostics))
    declarations = analysis.analyzer.generic_extensions.declarations(
        ARRAY_BASE_KEY, "count_plus_one")
    assert len(declarations) == 1
    assert declarations[0].type_params == ("T",)
