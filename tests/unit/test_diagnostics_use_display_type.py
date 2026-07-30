"""Diagnostics must render types through the `@(...)` display layer.

Types are interned under their `<...>` identity name (`StructType.name ==
"List<i32>"`), which mangling and the ~60 `startswith("List<")` predicates depend
on. That name must never reach the user: the language spells generics `List@(i32)`,
so a diagnostic showing `List<i32>` displays a syntax that no longer exists.

`semantics/generics/type_display.py` is the boundary. This test is the gate that
keeps every emit site behind it, in two parts:

  * no message template in `internals/errors/` hardcodes a `<...>` generic;
  * no `er.emit`/`emit_with`/`raise_internal_error`/`message_for` keyword argument
    interpolates a value (via `str(...)` or an f-string) that is not wrapped in
    `display_type` / `display_type_name`.

The second half is the one that regressed historically: `registry.py` formats with
`format_map`, so a bare `ty=`, `str(ty)`, and `f"{ty}"` all leak the identity name
identically, and nothing else in the pipeline would notice.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

# The `**kwargs` emit surface -- everything that reaches `text.format_map(...)`.
EMITTERS = {"emit", "emit_with", "raise_internal_error", "message_for"}

# Renderers that produce the `@(...)` surface form.
RENDERERS = {"display_type", "display_type_name"}

# A generic spelled with angle brackets, e.g. `List<T>` / `Result<T, E>`.
ANGLE_GENERIC = re.compile(r"[A-Z][A-Za-z0-9_]*<[A-Za-z_~{]")

# Keyword values that interpolate something which is provably not a type. Keyed by
# the unparsed source of the value expression, so it survives line renumbering.
ALLOWED_EXPRESSIONS = {
    # CE2070/CE2073 render the offending numeric literal's *value*, not a type.
    "str(value)",
    "str(node.value)",
}


def _iter_emit_calls():
    """Yield (path, ast.Call) for every diagnostic emit call in the compiler."""
    for path in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in EMITTERS:
                    yield path, node


def _renders_via_display(node: ast.AST) -> bool:
    """True if `node` is a `display_type(...)` / `display_type_name(...)` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name in RENDERERS


def _is_type_valued(node: ast.AST) -> bool:
    """True if `node` names something that holds a `Type`.

    Naming convention, not inference: the compiler consistently calls these
    `*_type` / `*_ty` / `.ty`. Most f-string interpolations in diagnostics are
    plain strings (`method_name`, `op`, `block.abi`) -- flagging those too would
    bury the real signal under ~40 false positives.
    """
    if isinstance(node, ast.Name):
        tail = node.id
    elif isinstance(node, ast.Attribute):
        tail = node.attr
    else:
        return False
    return tail == "ty" or tail.endswith("_ty") or tail.endswith("type")


def _leaks(value: ast.AST) -> bool:
    """True if `value` interpolates a type not routed through the display layer.

    Two shapes leak the interned name: `str(ty)` -- always suspect, since a type is
    the only reason to stringify into a diagnostic -- and an f-string embedding a
    type-valued name.
    """
    if _renders_via_display(value):
        return False
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name) and func.id == "str":
            return not (value.args and _renders_via_display(value.args[0]))
        return False
    if isinstance(value, ast.JoinedStr):
        return any(
            isinstance(part, ast.FormattedValue)
            and _is_type_valued(part.value)
            and not _renders_via_display(part.value)
            for part in value.values
        )
    return False


def test_no_emit_site_bypasses_display_type():
    offenders = []
    for path, call in _iter_emit_calls():
        for kw in call.keywords:
            if kw.arg is None:  # **kwargs splat
                continue
            if not _leaks(kw.value):
                continue
            src = ast.unparse(kw.value)
            if src in ALLOWED_EXPRESSIONS:
                continue
            rel = path.relative_to(ROOT.parent)
            offenders.append(f"{rel}:{kw.value.lineno}: {kw.arg}={src}")

    assert not offenders, (
        "Diagnostic argument(s) interpolate a value without display_type():\n  "
        + "\n  ".join(offenders)
        + "\n\nWrap the value in display_type() (or display_type_name() for a bare "
          "interned name string). If the value is provably not a type, add its "
          "source text to ALLOWED_EXPRESSIONS above."
    )


@pytest.mark.parametrize(
    "path",
    sorted((ROOT / "internals" / "errors").glob("*.py")),
    ids=lambda p: p.name,
)
def test_no_template_hardcodes_angle_generic(path):
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if ANGLE_GENERIC.search(line)
    ]
    assert not offenders, (
        "Message template(s) spell a generic with angle brackets; the language "
        "spells them `@(...)`:\n  " + "\n  ".join(offenders)
    )
