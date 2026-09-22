"""One argument check, and a ceiling on every copy that does not read it yet.

The shape `count the arguments -> for each one: propagate, walk, compare, report` was
written once per callee kind. A copy that falls behind is a behaviour the compiler has on
one path and has lost on another: the generics copy lost the bloom-spread refusal and the
borrow help (#747), and the FFI copy numbered its arguments from zero (#746). The seam is
`semantics/passes/types/arguments.py`, and this file is what stops a fourteenth copy.

The measurement is the EMIT site: a call to `er.emit`/`er.emit_with` naming CE2006 or
CE2009 as its code. A caller that hands the code to the seam names it as a KEYWORD and is
not an emit site, which is exactly the difference the gate is here to hold.
"""
from __future__ import annotations

import ast
from pathlib import Path

TYPES_PASS = (Path(__file__).resolve().parents[2]
              / "sushi_lang" / "semantics" / "passes" / "types")
SEAM = TYPES_PASS / "arguments.py"

#: The two codes the argument check owns.
_ARGUMENT_CODES = ("CE2006", "CE2009")

#: What each module that still holds a copy may emit, at MOST. A row may only go DOWN:
#: `arrays.py` (the built-in array arity checks) and `calls/methods.py` (the perk arm and
#: the extension arm) convert under their own tickets, and `calls/user_defined.py` keeps
#: the two checks that are not a parameter list at all -- the variadic TAIL, measured
#: against an element type, and the polymorphic math family, measured against a SET of
#: types. A module absent from this table may emit neither code.
_CEILING = {
    "arrays.py": {"CE2006": 2},
    "calls/methods.py": {"CE2006": 2, "CE2009": 2},
    "calls/user_defined.py": {"CE2006": 6, "CE2009": 2},
}

#: The reader above sees a code SPELLED at the emit. A module that carries the code in a
#: table row and emits `er.emit(reporter, row.code, ...)` is invisible to it, so a copy of
#: the loop could hide behind one indirection. These are the four positions that legitimately
#: do it -- the seam, whose codes travel IN, and the two tables that hold a code per row --
#: and the count is pinned so a fifth cannot appear unseen. A row may only go DOWN.
_INDIRECT_CEILING = {
    "arguments.py": 2,
    "arrays.py": 1,
    "calls/structs.py": 1,
}

#: The modules that must reach the seam, one per converted copy.
_CALLERS = (
    "calls/user_defined.py",
    "calls/statics.py",
    "calls/enums.py",
    "externals.py",
    "calls/dotcall.py",
)


def _emitted_code(node: ast.AST) -> str | None:
    """The registry code an `er.emit(...)` / `er.emit_with(...)` call names, if any."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in ("emit", "emit_with")):
        return None
    if len(node.args) < 2:
        return None
    code = node.args[1]
    if (isinstance(code, ast.Attribute)
            and isinstance(code.value, ast.Attribute) and code.value.attr == "ERR"):
        return code.attr
    return None


def _emit_sites() -> dict[str, dict[str, int]]:
    """Every CE2006/CE2009 emit site under the typecheck pass, by module."""
    found: dict[str, dict[str, int]] = {}
    for path in sorted(TYPES_PASS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            code = _emitted_code(node)
            if code in _ARGUMENT_CODES:
                module = path.relative_to(TYPES_PASS).as_posix()
                found.setdefault(module, {}).setdefault(code, 0)
                found[module][code] += 1
    return found


def test_the_detector_sees_an_emit_site():
    """The always-fires control: a sweep that answers zero must be a sweep that works."""
    tree = ast.parse("er.emit(r, er.ERR.CE2006, loc, index=1)\n"
                     "er.emit_with(r, er.ERR.CE2009, loc).emit()\n"
                     "check_arguments(v, n, p, a, loc, mismatch_code=er.ERR.CE2006)\n")
    seen = [_emitted_code(node) for node in ast.walk(tree)]
    assert [code for code in seen if code] == ["CE2006", "CE2009"], seen


def test_the_seam_exists_and_is_the_one_check():
    assert SEAM.is_file(), f"the argument check has no home at {SEAM}"
    tree = ast.parse(SEAM.read_text(encoding="utf-8"), filename=str(SEAM))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "check_arguments" in names, sorted(names)


def test_the_seam_emits_neither_code_by_name():
    """The codes travel IN, so the seam never spells one: it serves CE2049/CE2050 too."""
    sites = _emit_sites()
    assert "arguments.py" not in sites, sites.get("arguments.py")


def _indirect_emit_sites() -> dict[str, int]:
    """Every emit under the pass whose code is a value, not a spelled `er.ERR.CExxxx`."""
    found: dict[str, int] = {}
    for path in sorted(TYPES_PASS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in ("emit", "emit_with")):
                continue
            spelled = any(isinstance(arg, ast.Attribute)
                          and isinstance(arg.value, ast.Attribute)
                          and arg.value.attr == "ERR"
                          for arg in node.args[:2])
            if not spelled:
                module = path.relative_to(TYPES_PASS).as_posix()
                found[module] = found.get(module, 0) + 1
    return found


def test_no_emit_hides_its_code_behind_a_value():
    sites = _indirect_emit_sites()
    offenders = [f"{module}: {count}, ceiling {_INDIRECT_CEILING.get(module, 0)}"
                 for module, count in sorted(sites.items())
                 if count > _INDIRECT_CEILING.get(module, 0)]
    assert not offenders, (
        "an emit names its code through a value, where the ceiling above cannot read it:"
        "\n  " + "\n  ".join(offenders)
        + "\nSpell the code at the emit, or route the position through "
          "passes/types/arguments.py:check_arguments."
    )


def test_the_indirect_detector_sees_a_site():
    """The always-fires control for the reader above."""
    assert _indirect_emit_sites(), "the indirect reader found nothing at all"


def test_no_copy_of_the_argument_loop_grows():
    sites = _emit_sites()
    offenders: list[str] = []
    for module, counts in sorted(sites.items()):
        allowed = _CEILING.get(module, {})
        for code, count in sorted(counts.items()):
            if count > allowed.get(code, 0):
                offenders.append(
                    f"{module}: {count} x {code}, ceiling {allowed.get(code, 0)}")
    assert not offenders, (
        "a copy of the argument loop grew:\n  " + "\n  ".join(offenders)
        + "\nRoute the position through passes/types/arguments.py:check_arguments "
          "instead. A row of the ceiling table may only go DOWN."
    )


def test_every_converted_caller_reaches_the_seam():
    missing = [module for module in _CALLERS
               if "check_arguments(" not in (TYPES_PASS / module).read_text(encoding="utf-8")]
    assert not missing, (
        "a converted caller no longer names the seam: " + ", ".join(missing))
