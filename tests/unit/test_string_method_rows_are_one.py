"""A string method's signature is ONE row, and every reader takes it from there (#828).

A string method used to be spelled in four places: the argument list in `METHOD_SPECS`,
a return-type ladder beside it, a 21-arm ladder in the back end that spelled the LLVM
types again, and the generators. Nothing checked that the four agreed. Now the row
holds the parameters and the return, the return-type reader and the back end both read
it, and this gate compares the row with what the generator built.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.collections.strings import (
    METHOD_SPECS,
    MethodSpec,
    get_builtin_string_method_return_type,
    generate_module_ir,
)

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
EMITTER = ROOT / "backend" / "expressions" / "calls" / "stdlib" / "strings.py"
NAMES = sorted(METHOD_SPECS)


def test_the_rows_are_measured():
    """The control: the table holds the names the old ladder spelled."""
    assert len(METHOD_SPECS) == 33
    for name in ("len", "join", "pad_left", "find_last", "to_f64", "split"):
        assert name in METHOD_SPECS


@pytest.mark.parametrize("name", NAMES)
def test_a_row_holds_every_parameter_and_the_return(name):
    spec = METHOD_SPECS[name]
    assert isinstance(spec, MethodSpec)
    assert spec.arg_count == len(spec.arg_types)
    assert spec.returns is not None


@pytest.mark.parametrize("name", NAMES)
def test_the_return_type_reader_answers_from_the_row(name):
    assert get_builtin_string_method_return_type(name, BuiltinType.STRING) == METHOD_SPECS[name].returns


@pytest.mark.parametrize("name", NAMES)
def test_the_backend_declares_what_the_generator_built(name):
    from sushi_lang.backend.expressions.calls.stdlib.strings import string_method_function_type

    generated = {f.name: f.function_type for f in generate_module_ir().functions}
    symbol = f"string_{name}"
    assert symbol in generated, f"no generated {symbol}"
    assert str(string_method_function_type(METHOD_SPECS[name])) == str(generated[symbol])


def test_the_backend_spells_no_method_name():
    tree = ast.parse(EMITTER.read_text())
    spelled = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not sorted(spelled & set(METHOD_SPECS)), "the emitter spells a method name"
