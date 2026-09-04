"""One table per stdlib layer, and every reader takes its row from it (#550).

The socket list used to be spelled in four Python places beside its own name list, and
`files_funcs.py` spelled its own names in three if/elif chains inside one file. A name
missing from one place answered CE2008 for a function the compiler can emit.

This is the gate, in the shape of `test_callee_mode_matrix.py`: for every name in a
layer's table, every reader must have an answer, and the answers must agree.

The readers:

| reader | what it takes from the row |
|---|---|
| `semantics/stdlib_registry.py` | the parameter types |
| the module's `get_builtin_*_return_type` | the Ok type and the error enum |
| the module's `validate_*_call` | the arity |
| `semantics/generics/instantiate/expressions.py` | the Result (and Maybe) to intern |
| `backend/expressions/calls/stdlib/` | the LLVM parameter types and the marshalling |
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import Type
from sushi_lang.sushi_stdlib.src.io.files_funcs import (
    FILE_UTILITY_FUNCTIONS,
    FILES_SIGNATURES,
    get_builtin_files_function_return_type,
)
from sushi_lang.sushi_stdlib.src.net.socket_funcs import (
    SOCKET_FUNCTIONS,
    SOCKET_SIGNATURES,
    get_builtin_socket_function_return_type,
)
from sushi_lang.sushi_stdlib.src.signatures import Signature

LAYERS = (
    ("socket", SOCKET_SIGNATURES, SOCKET_FUNCTIONS, get_builtin_socket_function_return_type),
    ("files", FILES_SIGNATURES, FILE_UTILITY_FUNCTIONS, get_builtin_files_function_return_type),
)
LAYER_IDS = [name for name, *_ in LAYERS]

EVERY_ROW = [(layer, name, sig)
             for layer, table, *_ in LAYERS
             for name, sig in table.items()]
ROW_IDS = [f"{layer}:{name}" for layer, name, _sig in EVERY_ROW]


# -- the table is the name list ------------------------------------------------

@pytest.mark.parametrize(("layer", "table", "names", "_ret"), LAYERS, ids=LAYER_IDS)
def test_the_table_and_the_name_list_are_the_same_set(layer, table, names, _ret):
    assert set(table) == set(names), (
        f"{layer}: the name list and the signature table disagree; "
        f"only in the list: {sorted(set(names) - set(table))}, "
        f"only in the table: {sorted(set(table) - set(names))}")


@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_every_row_is_a_signature(layer, name, sig):
    assert isinstance(sig, Signature)
    for param in sig.params:
        assert isinstance(param.ty, Type | GenericTypeRef), f"{layer}:{name}"


# -- the readers ---------------------------------------------------------------

@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_the_registry_reads_the_table(layer, name, sig):
    """The registry's parameter spec IS the row, never a second spelling."""
    from sushi_lang.semantics.stdlib_registry import _get_param_specs

    specs = _get_param_specs()
    module = "socket" if layer == "socket" else "files"
    assert (module, name) in specs, f"the registry has no spec for {name}"
    assert specs[(module, name)] == [param.ty for param in sig.params]


@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_the_return_type_comes_from_the_table(layer, name, sig):
    reader = dict((lay, ret) for lay, _t, _n, ret in LAYERS)[layer]
    answered = reader(name)
    if sig.ok is None:
        assert answered == sig.bare, f"{name} answers its value bare"
        return
    assert isinstance(answered, GenericTypeRef)
    assert answered.base_name == "Result"
    assert answered.type_args[0] == sig.ok


@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_the_arity_comes_from_the_table(layer, name, sig):
    """A wrong count is CE2009, and the count is the row's own length."""
    from sushi_lang.internals.report import Reporter

    reader = {"socket": "validate_socket_function_call",
              "files": "validate_files_function_call"}[layer]
    module = ("sushi_lang.sushi_stdlib.src.net.socket_funcs" if layer == "socket"
              else "sushi_lang.sushi_stdlib.src.io.files_funcs")
    validate = getattr(__import__(module, fromlist=[reader]), reader)

    right = Reporter()
    validate(name, [None] * len(sig.params), right, None)
    assert not right.has_errors, f"{name} refused its own arity"

    wrong = Reporter()
    validate(name, [None] * (len(sig.params) + 1), wrong, None)
    assert wrong.has_errors, f"{name} accepted one argument too many"


@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_the_backend_can_cross_every_type_in_the_row(layer, name, sig):
    """Every parameter type and every Ok type has an LLVM shape and a marshaller."""
    from sushi_lang.backend.expressions.calls.stdlib.signatures import (
        llvm_ok_type,
        llvm_param_type,
    )

    for param in sig.params:
        assert llvm_param_type(param) is not None, f"{layer}:{name} parameter {param.ty}"
    if sig.ok is not None:
        assert llvm_ok_type(sig.ok) is not None, f"{layer}:{name} ok {sig.ok}"


@pytest.mark.parametrize(("layer", "name", "sig"), EVERY_ROW, ids=ROW_IDS)
def test_a_row_that_answers_a_result_names_an_error_enum(layer, name, sig):
    """The instantiate pass interns `Result@(ok, E)`, so E has to be named."""
    if sig.ok is None:
        assert sig.error is None, f"{name} answers bare and needs no error enum"
    else:
        assert sig.error, f"{name} answers a Result and must name its error enum"


# -- the shape of a row --------------------------------------------------------

def test_a_cstr_parameter_is_a_string_that_crosses_as_a_pointer():
    """`fd_open` takes a PATH and `fd_write_str` a string VALUE: one type, two crossings."""
    from sushi_lang.semantics.typesys import BuiltinType

    path = FILES_SIGNATURES["fd_open"].params[0]
    value = FILES_SIGNATURES["fd_write_str"].params[1]
    assert path.ty == BuiltinType.STRING and path.as_cstr
    assert value.ty == BuiltinType.STRING and not value.as_cstr


def test_the_socket_layer_answers_neterror_throughout():
    """Every <net/socket> primitive answers Result@(T, NetError) -- the module says so."""
    assert {sig.error for sig in SOCKET_SIGNATURES.values()} == {"NetError"}
