"""Every borrow position asks ONE gate what this name's record permits (#685, #713).

CE2400 -- the borrow refusal -- was written at five sites with four predicates, and the
fourth read the FLAT constant table. `by_name` is first-wins over the whole program, so a
unit whose own `var` shares a name with another unit's `const` was refused, and a unit
whose own `const` shares a name with another unit's `var` was let through to write
read-only memory. The multi-unit halves of that measurement are fixtures under
`tests/references/borrow_of_constant/`; the matrix below is the single-unit half, plus the
gate that keeps the emission in one place.

The matrix carries the MODE since #713. A constant is read-only STORAGE: `peek` reads
through a pointer into `.rodata`, and only a write -- `poke`, or the `nom` take -- is the
fault. Before the ruling the POSITION decided: three of the four `peek` positions refused
and the match arm allowed it, while a bare pattern binding, which the ownership model also
calls a borrow, was allowed in all of them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import Provenance
from sushi_lang.semantics.passes.borrow.borrows import check_borrow
from sushi_lang.semantics.passes.borrow.consume import source_provenance
from sushi_lang.semantics.passes.collect import ConstSig
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.body import Initializer, Instance
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = SOURCE_ROOT / "semantics" / "constant_borrow.py"


# --- The seam: one emit site for the code, and every caller reads the scoped lookup.

def _emitters_of(code: str) -> list[str]:
    """Every source line that hands `code` to an emitter, with its file and line."""
    hits: list[str] = []
    pattern = re.compile(rf"\ber\.(?:ERR\.)?{code}\b")
    for path in SOURCE_ROOT.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not pattern.search(line):
                continue
            hits.append(f"{path.relative_to(SOURCE_ROOT)}:{number}")
    return hits


def test_the_code_is_emitted_from_one_place_only():
    """A sixth emit site outside the seam is what #685 closed; keep it closed."""
    hits = [hit for hit in _emitters_of("CE2400")
            if not hit.startswith("semantics/constant_borrow.py")]
    assert not hits, (
        f"CE2400 is emitted outside the seam at: {hits}.\n"
        "Call `reject_borrow_of_constant` instead. Five sites with four predicates is "
        "what #685 collapsed, and one of them read the FLAT constant table."
    )


def test_the_seam_exists_and_holds_the_gate():
    assert SEAM.is_file(), f"the seam module is missing: {SEAM}"
    tree = ast.parse(SEAM.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
    assert "reject_borrow_of_constant" in names, sorted(names)


def test_no_caller_reads_the_flat_constant_table():
    """`by_name` is program-wide and first-wins; the question is per unit."""
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "const_table.by_name.get(" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{number}")
    assert not offenders, (
        f"a flat constant lookup stands at: {offenders}.\n"
        "Use the scoped `const_sig(name)` / `const_table.lookup(name, unit, scope)`: "
        "the flat view is first-wins across the whole program, so it answers with "
        "another unit's declaration of the same name (#685)."
    )


def test_every_caller_names_the_mode():
    """The mode is what the record is asked about, so no caller may leave it out (#713)."""
    text = SEAM.read_text(encoding="utf-8")
    tree = ast.parse(text)
    gate = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "reject_borrow_of_constant")
    assert [arg.arg for arg in gate.args.kwonlyargs][:1] == ["mode"], ast.dump(gate.args)
    assert gate.args.kw_defaults[0] is None, "the mode has a default, so a caller can skip it"


# --- The SHIR caller: the borrow pass asks the seam at a `ConstRef` place (ir.md 7.9).

def _calls_of_the_seam(path: Path) -> list[int]:
    """The line of each call of the seam in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "reject_borrow_of_constant"]


def test_the_borrow_pass_asks_the_seam_from_one_arm():
    """The borrow of a place is the one arm: a marked argument, a `Borrow` and the value of
    a reference `let` all reach it."""
    borrow = SOURCE_ROOT / "semantics" / "passes" / "borrow"
    calls = {path.name: _calls_of_the_seam(path) for path in sorted(borrow.glob("*.py"))}
    callers = {name: lines for name, lines in calls.items() if lines}
    assert list(callers) == ["borrows.py"] and len(callers["borrows.py"]) == 1, callers


def test_lowering_refuses_no_borrow_of_a_constant():
    """A `poke` of a constant is a `Borrow` of a `ConstRef` in SHIR, and no error hole: the
    borrow pass reports CE2400, so lowering holds no copy of the rule."""
    shir = SOURCE_ROOT / "semantics" / "shir"
    offenders = [f"{path.name}:{number}" for path in sorted(shir.glob("*.py"))
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if '"CE2400"' in line]
    assert not offenders, offenders


_AT = Span(7, 15, 7, 21)


class _Diagnostic:
    def __init__(self, sink: list, code: object, span: object, params: dict) -> None:
        self.sink, self.code, self.span, self.params = sink, code, span, params

    def help(self, _text: str) -> "_Diagnostic":
        return self

    def emit(self) -> None:
        self.sink.append((self.code, self.span, self.params))


class _Errors:
    def __init__(self) -> None:
        self.emitted: list = []

    def emit_with(self, code: object, span: object, **params: object) -> _Diagnostic:
        return _Diagnostic(self.emitted, code, span, params)


def _checker(record: object) -> SimpleNamespace:
    """A stand-in for the pass whose lookup answers `record`: a `ConstSig`, or the record of
    a stdlib constant."""
    decls = DeclNames()
    return SimpleNamespace(err=_Errors(), decls=decls, decl_name=decls.name_of,
                           constant_record=lambda _node: record)


def _ref(checker: SimpleNamespace, written: str) -> e.ConstRef:
    return e.ConstRef(ShirId(1), _AT, checker.decls.refer(written))


_CONSTANT = ConstSig(name="STEP", const_type=BuiltinType.I32)
_VARIABLE = ConstSig(name="count", const_type=BuiltinType.I32, is_var=True)


@pytest.mark.parametrize("written", ["STEP", "t.STEP"], ids=["bare", "behind_an_alias"])
def test_a_poke_of_a_constant_is_refused(written: str):
    checker = _checker(_CONSTANT)
    check_borrow(checker, _ref(checker, written), BorrowMode.POKE, _AT)  # type: ignore[arg-type]
    assert checker.err.emitted == [(er.ERR.CE2400, _AT, {"name": "STEP", "mode": "poke"})]


def test_a_peek_of_a_constant_reads_it():
    """A `peek` of read-only storage reads it (#713)."""
    checker = _checker(_CONSTANT)
    check_borrow(checker, _ref(checker, "STEP"), BorrowMode.PEEK, _AT)  # type: ignore[arg-type]
    assert checker.err.emitted == []


@pytest.mark.parametrize("mode", [BorrowMode.PEEK, BorrowMode.POKE], ids=["peek", "poke"])
@pytest.mark.parametrize("written", ["PI", "m.PI"], ids=["bare", "behind_an_alias"])
def test_a_stdlib_constant_has_no_storage(written: str, mode: BorrowMode):
    """A stdlib constant has a record that is no `ConstSig`, and no storage, so both modes are
    refused."""
    checker = _checker(lookup_stdlib_constant("PI", None))
    check_borrow(checker, _ref(checker, written), mode, _AT)  # type: ignore[arg-type]
    assert checker.err.emitted == [(er.ERR.CE2400, _AT, {"name": "PI", "mode": mode.value})]


def test_the_record_decides_and_not_the_name():
    """A `var` takes both modes. A `ConstRef` never names one; the record decides all the
    same."""
    checker = _checker(_VARIABLE)
    check_borrow(checker, _ref(checker, "count"), BorrowMode.POKE, _AT)  # type: ignore[arg-type]
    assert checker.err.emitted == []


# --- An initializer body: the constant evaluator makes its value (ir.md 7.9).

def _in(origin: object) -> SimpleNamespace:
    return SimpleNamespace(body=SimpleNamespace(origin=origin))


def test_a_constant_that_an_initializer_names_is_not_moved():
    """`const string ALIAS = GREETING` reads `GREETING`: the value is FRESH, so the `Return`
    of the initializer adopts it and no CE2436 is reported."""
    ref = e.ConstRef(ShirId(1), _AT, DeclNames().refer("GREETING"))
    assert source_provenance(_in(Initializer()), ref) is Provenance.FRESH  # type: ignore[arg-type]


def test_a_constant_in_a_function_body_is_storage_the_program_keeps():
    ref = e.ConstRef(ShirId(1), _AT, DeclNames().refer("GREETING"))
    assert source_provenance(_in(Instance()), ref) is Provenance.OWNED  # type: ignore[arg-type]
