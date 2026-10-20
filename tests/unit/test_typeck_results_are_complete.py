"""The completeness check of `TypeckResults`, and the rule of one answer per key (docs/design/ir.md 7.3).

The `typecheck` pass writes every answer that it gives into the table of the body (ruling
S5). When the pass ends, the check walks the body and finds each answer that a node needs
and that the table does not hold. A gap is the internal error CE0156. These tests give the
check a body and a table that are made by hand (`typeck_sample.py`): a full table has no
gap, and a table with no row of one kind has the gap of that row. They call a Python
function over data; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.shir.typeck_check import MissingAnswer, check_answers, missing_answers
from sushi_lang.semantics.typesys import BuiltinType

from typeck_sample import LOCAL_TYPES, ROWS, answers, sample, span


def _gap(answer: str, node: str, line: int) -> MissingAnswer:
    return MissingAnswer(answer, node, span(line))


def test_a_full_table_has_no_gap() -> None:
    made = sample()
    assert missing_answers(made.body, answers(made)) == []
    check_answers(made.body, answers(made), made.decls)


#: The row that a table leaves out -> the gap that the check finds.
GAPS: dict[str, MissingAnswer] = {
    "callee decorate": _gap("callee", "Call", 2),
    "callee shout": _gap("callee", "Call", 4),
    "callee println": _gap("callee", "Call", 7),
    "convention decorate 0": _gap("convention of argument 0", "Call", 2),
    "convention shout 0": _gap("convention of argument 0", "Call", 4),
    "convention println 0": _gap("convention of argument 0", "Call", 7),
    "instance inner_tuple": _gap("instance type", "TupleInit", 5),
    "instance outer_tuple": _gap("instance type", "TupleInit", 5),
    "instance struct": _gap("instance type", "StructInit", 11),
    "instance enum": _gap("instance type", "EnumInit", 12),
    "field index": _gap("field index", "Field", 11),
    "loop kind": _gap("loop kind", "Loop", 6),
    "range value": _gap("range value", "Range", 6),
    "conversion try": _gap("conversion", "Try", 2),
    "conversion cast": _gap("conversion", "Cast", 8),
    "try facts": _gap("try facts", "Try", 2),
    "display interp 1": _gap("display type of part 1", "Interp", 7),
    "display println 0": _gap("display type of argument 0", "Call", 7),
    "match facts": _gap("scrutinee type", "Match", 8),
    "element 0": _gap("element type of target 0", "Destructure", 5),
    "element 1": _gap("element type of target 1", "Destructure", 5),
    "element 1.0": _gap("element type of target 1.0", "Destructure", 5),
    "element 1.1": _gap("element type of target 1.1", "Destructure", 5),
}


def test_each_row_of_the_sample_has_a_gap_case() -> None:
    assert set(GAPS) | {"locals", "types"} == set(ROWS)


@pytest.mark.parametrize("row", sorted(GAPS))
def test_a_missing_row_is_found(row: str) -> None:
    made = sample()
    assert missing_answers(made.body, answers(made, skip=row)) == [GAPS[row]]


@pytest.mark.parametrize("index", range(len(sample().exprs)))
def test_an_expression_with_no_type_is_found(index: int) -> None:
    made = sample()
    node = made.exprs[index]
    gaps = missing_answers(made.body, answers(made, skip_type=node))
    assert gaps == [MissingAnswer("type", type(node).__name__, node.loc)]


@pytest.mark.parametrize("index", sorted(LOCAL_TYPES))
def test_a_local_with_no_type_is_found(index: int) -> None:
    made = sample()
    by_id = {local.id.index: local for local in made.body.locals}
    gaps = missing_answers(made.body, answers(made, skip_local=index))
    name = by_id[index].name
    assert gaps == [MissingAnswer("type", f'local _{index} "{name}"', by_id[index].loc)]


def test_an_empty_table_misses_every_row() -> None:
    made = sample()
    gaps = missing_answers(made.body, TypeckResults())
    assert len(gaps) == len(LOCAL_TYPES) + len(made.exprs) + len(GAPS)


def test_a_gap_is_the_internal_error_ce0156() -> None:
    made = sample()
    with pytest.raises(InternalCompilerError) as caught:
        check_answers(made.body, answers(made, skip="loop kind"), made.decls)
    error = caught.value
    assert error.code == "CE0156"
    assert error.params == {"answer": "loop kind", "node": "Loop", "where": "line 6, column 5",
                            "body": "sample"}
    assert error.span == made.nodes["loop"].loc  # type: ignore[attr-defined]


def test_the_first_gap_is_the_one_that_the_error_names() -> None:
    made = sample()
    with pytest.raises(InternalCompilerError) as caught:
        check_answers(made.body, answers(made, skip_local=2, skip="field index"), made.decls)
    assert caught.value.params["node"] == 'local _2 "msg"'


# -- one answer per key ----------------------------------------------------------------


@pytest.mark.parametrize("write", [
    lambda table: table.record_type(ShirId(0), BuiltinType.I32),
    lambda table: table.record_local(LocalId(0), BuiltinType.I32),
    lambda table: table.record_convention(ShirId(0), 0, Convention.OWNED),
], ids=["type", "local", "convention"])
def test_a_second_answer_for_one_key_is_an_internal_error(write) -> None:
    table = TypeckResults()
    write(table)
    with pytest.raises(InternalCompilerError) as caught:
        write(table)
    assert caught.value.code == "CE0000"


def test_two_positions_of_one_call_are_two_keys() -> None:
    table = TypeckResults()
    table.record_convention(ShirId(0), 0, Convention.OWNED)
    table.record_convention(ShirId(0), 1, Convention.GUARANTEED)
    assert table.convention_of(ShirId(0), 1) is Convention.GUARANTEED
    assert table.convention_of(ShirId(0), 2) is None


def test_a_read_of_a_key_with_no_answer_is_none() -> None:
    table = TypeckResults()
    assert table.type_of(ShirId(3)) is None
    assert table.local_type(LocalId(3)) is None
    assert table.callee_of(ShirId(3)) is None
