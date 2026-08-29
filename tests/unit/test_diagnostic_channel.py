"""The diagnostic channel is gated: what a call site says must render (#374).

Two static checks over every emit site in `sushi_lang/`. Check 1: a plain
string literal that contains a `{` must not reach an emitter -- either the `f`
prefix is missing or the brace must be doubled. Check 2: the kwargs at a call
site must match the placeholders of the registered template exactly -- a kwarg
the template does not name is dead, and a placeholder no kwarg supplies
renders as `<missing:key>`. Both are the class of defect behind #270 and #271.

No allowlist. An exemption needs a reason and a tracking issue.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
SOURCE_ROOT = REPO / "sushi_lang"

# The four ways a message reaches the channel. `emit_exception` renders an
# exception's own params and has no literal call-site text to check.
EMITTERS = {"emit", "emit_with", "message_for", "raise_internal_error"}

CODE_RE = re.compile(r"^(CE|CW|RE)\d{4}$")

# Kwargs the emitters consume themselves, never the template.
CHANNEL_KWARGS = {"filename"}


def _placeholders(text: str) -> set[str]:
    bare = text.replace("{{", "").replace("}}", "")
    return set(re.findall(r"\{(\w+)[^{}]*\}", bare))


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _code_of(call: ast.Call) -> str | None:
    """The diagnostic code a call names, when it names one statically."""
    for arg in call.args:
        if isinstance(arg, ast.Attribute) and CODE_RE.match(arg.attr):
            return arg.attr
        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and CODE_RE.match(arg.value)):
            return arg.value
    return None


def _emit_calls():
    for py in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee_name(node) in EMITTERS:
                yield py.relative_to(REPO), node


def test_no_unformatted_placeholder_reaches_an_emitter():
    offenders = []
    for path, call in _emit_calls():
        literals = [a for a in call.args if isinstance(a, ast.Constant)]
        literals += [k.value for k in call.keywords
                     if k.arg is not None and isinstance(k.value, ast.Constant)]
        for lit in literals:
            if not isinstance(lit.value, str) or CODE_RE.match(lit.value):
                continue
            if "{" in lit.value.replace("{{", ""):
                offenders.append(
                    f"{path}:{call.lineno}: literal {lit.value[:50]!r} holds an "
                    f"unformatted '{{' (missing f-prefix, or double the brace)")
    assert not offenders, "\n" + "\n".join(offenders)


def test_call_site_kwargs_match_the_registered_template():
    from sushi_lang.internals.errors import REGISTRY

    offenders = []
    for path, call in _emit_calls():
        name = _callee_name(call)
        code = _code_of(call)
        if code is None:
            continue
        if any(k.arg is None for k in call.keywords):
            continue  # **kwargs forwarding: not statically checkable
        registered = REGISTRY.get(code)
        if registered is None:
            continue  # test_error_registry owns unregistered codes
        if name == "emit_with" or name == "emit":
            kwargs = {k.arg for k in call.keywords} - CHANNEL_KWARGS
        else:
            kwargs = {k.arg for k in call.keywords}
        wanted = _placeholders(registered.text)
        dead = sorted(kwargs - wanted)
        missing = sorted(wanted - kwargs)
        if dead or missing:
            offenders.append(
                f"{path}:{call.lineno}: {code} takes {sorted(wanted)!r}; "
                f"dead kwargs {dead!r}, missing {missing!r}")
    assert not offenders, "\n" + "\n".join(offenders)


def test_the_gate_sees_the_channel():
    """The scan is not vacuous: the tree holds emit sites it can read."""
    calls = list(_emit_calls())
    assert len(calls) > 300, f"only {len(calls)} emit sites found; the scan is broken"
    coded = [c for _p, c in calls if _code_of(c) is not None]
    assert len(coded) > 300


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
