"""The extend_def signature slots: method-level type params and the error channel.

`extend <target> NAME [@(type_params)] "(" [parameters] ")" type ["|" type] ":" block`
is the Phase 1 grammar of the UFCS-combinators epic. This module pins the parse level
only: each new slot populates the `ExtendDef` node, and each combination of the two
optional slots parses (LALR, `maybe_placeholders=False` -- an omitted optional emits
no placeholder child, so the builder must count type nodes rather than index them).
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast


def _sole_extension(src: str):
    program, _tree = parse_to_ast(src)
    extensions = list(program.extensions) + list(program.generic_extensions)
    assert len(extensions) == 1, f"expected one extension, got {len(extensions)}"
    return extensions[0]


# -- the 4-way optional matrix ---------------------------------------------------

BARE = (
    "extend i32 twice() i32:\n"
    "    return self * 2\n"
)

MARGS_ONLY = (
    "extend i32 dup@(U)(U x) U:\n"
    "    return x\n"
)

ERR_ONLY = (
    "extend i32 checked() i32 | StdError:\n"
    "    return self\n"
)

BOTH = (
    "extend i32 mapv@(U)(fn(i32) -> U f) U | StdError:\n"
    "    return f(self)??\n"
)


@pytest.mark.parametrize("case_id,src,has_margs,has_err", [
    ("bare", BARE, False, False),
    ("margs_only", MARGS_ONLY, True, False),
    ("err_only", ERR_ONLY, False, True),
    ("both", BOTH, True, True),
])
def test_optional_slots_populate_exactly_when_written(case_id, src, has_margs, has_err):
    ext = _sole_extension(src)

    if has_margs:
        assert ext.type_params is not None, f"{case_id}: @(...) written, type_params empty"
        assert [tp.name for tp in ext.type_params] == ["U"]
    else:
        assert ext.type_params is None, f"{case_id}: no @(...), type_params populated"

    if has_err:
        assert ext.err_type is not None, f"{case_id}: | E written, err_type empty"
        assert ext.err_span is not None, f"{case_id}: | E written, err_span empty"
    else:
        assert ext.err_type is None, f"{case_id}: no | E, err_type populated"
        assert ext.err_span is None, f"{case_id}: no | E, err_span populated"


@pytest.mark.parametrize("case_id,src", [
    ("bare", BARE),
    ("margs_only", MARGS_ONLY),
    ("err_only", ERR_ONLY),
    ("both", BOTH),
])
def test_existing_slots_survive_the_new_optionals(case_id, src):
    """Name, return type and body populate in every combination."""
    ext = _sole_extension(src)

    assert ext.ret is not None, f"{case_id}: return type missing"
    assert ext.body is not None and ext.body.statements, f"{case_id}: body missing"
    assert ext.name_span is not None, f"{case_id}: name span missing"
    assert ext.ret_span is not None, f"{case_id}: return span missing"


def test_err_type_is_the_declared_error_name():
    ext = _sole_extension(
        "extend i32 halve() i32 | MathError:\n"
        "    return self / 2\n"
    )
    assert ext.err_type is not None
    assert "MathError" in str(ext.err_type)


def test_two_method_type_params_parse():
    ext = _sole_extension(
        "extend i32 pairv@(U, V)(U a, V b) U:\n"
        "    return a\n"
    )
    assert ext.type_params is not None
    assert [tp.name for tp in ext.type_params] == ["U", "V"]


def test_generic_target_keeps_its_routing_with_the_new_slots():
    """A @(...) target still lands in generic_extensions with margs and | E present."""
    program, _tree = parse_to_ast(
        "extend List@(T) mapv@(U)(fn(T) -> U f) List@(U) | StdError:\n"
        "    return List.new()\n"
    )
    assert len(program.generic_extensions) == 1
    assert not program.extensions
    ext = program.generic_extensions[0]
    assert ext.type_params is not None
    assert [tp.name for tp in ext.type_params] == ["U"]
    assert ext.err_type is not None
