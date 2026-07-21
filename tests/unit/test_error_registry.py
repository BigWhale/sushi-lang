"""The diagnostic registry must be complete, consistent, and renderable.

Every code the compiler can emit has to be IN the registry, or the compiler
crashes while reporting -- which is exactly how `r.is_ok(1)` used to produce an
`AttributeError: CE2016` traceback instead of a diagnostic. These tests scan the
source for referenced codes and check them against the registry, so a code that
is referenced but never registered turns the suite red at CI time rather than at
a user.
"""
from __future__ import annotations

import re
from pathlib import Path


from sushi_lang.internals.errors import REGISTRY, Category, Severity, _fmt

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
TEST_ROOT = Path(__file__).resolve().parents[1]

# How a diagnostic code is referenced in compiler source.
REFERENCE_PATTERNS = [
    re.compile(r"ERR\.(C[EW]\d{4})"),
    re.compile(r"""ERR\[["'](C[EW]\d{4})["']\]"""),
    re.compile(r"""raise_internal_error\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""SushiError\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""SyntaxDiagnostic\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""InternalCompilerError\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""StdlibBuildError\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""AstBuilderICE\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""LibraryError\(\s*["'](C[EW]\d{4})["']"""),
    re.compile(r"""emit_runtime_error(?:_with_values)?\(\s*["'](RE\d{4})["']"""),
]

# The number of registered codes. Bumping this is a deliberate act: it is the
# tripwire for silent loss when errors.py is split into a package.
# 261: deleted 17 genuinely-dead speculative codes (CE0001/37/38/39/48/63/66/70/82/84/
# 86/88/97/98, CE2022, CE3503, CE3506) that nothing emitted -- Tier 4.8 PR4 hygiene.
REGISTRY_SIZE = 263  # Tier 6.0: -CE4008 -CE4009 (unreachable, deleted) +CE4010; +CE2062 +CE6102; #134 +CE0127

# Codes whose numeric range does not match their category. SHRINK-ONLY: never add.
# Renumbering would break EXPECT_ERROR_CODE headers and the docs, so these stay
# put and are named here instead.
RANGE_EXEMPT = {
    "CE0004",  # duplicate struct         -- a TYPE error in the internal range
    "CE0005",  # duplicate field          -- a TYPE error in the internal range
    "CE0006",  # enum/struct name clash   -- a TYPE error in the internal range
    "CE0122",  # infinitely recursive generic -- a TYPE error in the internal range
    "CE2061",  # an INTERNAL error living in the type range
}


def _referenced_codes() -> set[str]:
    found: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in REFERENCE_PATTERNS:
            found.update(pattern.findall(text))
    return found


def test_every_referenced_code_is_registered():
    """A code the compiler can emit but never registered is a crash waiting to fire."""
    unregistered = sorted(_referenced_codes() - set(REGISTRY))
    assert not unregistered, (
        f"referenced but not registered: {unregistered}. "
        "Register them, or retarget the call site to an existing code."
    )


def test_registry_size():
    assert len(REGISTRY) == REGISTRY_SIZE, (
        f"registry has {len(REGISTRY)} codes, expected {REGISTRY_SIZE}. "
        "If you added or removed a code deliberately, update REGISTRY_SIZE."
    )


def test_every_registry_text_formats():
    """Every text renders with its own placeholders -- checked here, not at a user."""
    for code, msg in REGISTRY.items():
        params = {name: f"<{name}>" for name in
                  re.findall(r"\{(\w+)\}", msg.text)}
        rendered = _fmt(code, **params)
        assert "<missing:" not in rendered, f"{code}: {rendered}"


def test_missing_format_key_degrades_instead_of_raising():
    """The error machinery must not crash while reporting an error."""
    rendered = _fmt("CE0073")  # CE0073's text needs {type}; deliberately omitted
    assert rendered == "unknown primitive type: <missing:type>"


def test_unknown_code_degrades_instead_of_raising():
    from sushi_lang.internals.errors import ERR

    msg = ERR.CE9999
    assert msg.code == "CE9999"
    assert msg.category is Category.INTERNAL
    assert "compiler bug" in msg.text


def _category_of_range(code: str) -> set[Category]:
    """The categories a code's numeric range is allowed to carry.

    Mirrors the module each code lives in after the errors/ package split -- a code
    can only be ADDED in the file that owns its range, and this pins the category
    that file assigns.
    """
    if code.startswith("CW"):
        return set(Category)  # warnings span every category
    if code.startswith("RE"):
        return {Category.RUNTIME}
    number = int(code[2:])
    if number < 100:
        return {Category.INTERNAL, Category.GENERAL}
    if number < 200:
        return {Category.FUNC, Category.INTERNAL, Category.TYPE}
    if number < 1000:
        return {Category.INTERNAL, Category.TYPE}
    if number < 2000:
        return {Category.SCOPE}
    if number < 2400:
        return {Category.TYPE}
    if number < 2500:
        return {Category.BORROW}
    if number < 3000:
        return {Category.TYPE}      # Result/Maybe method errors are type errors
    if number < 3500:
        return {Category.UNIT}
    if number < 4000:
        return {Category.LIBRARY}
    if number < 5000:
        return {Category.PERK}
    if number < 6000:
        return {Category.FFI}
    return {Category.SYNTAX}


def test_category_matches_range():
    violations = sorted(
        code for code, msg in REGISTRY.items()
        if code not in RANGE_EXEMPT and msg.category not in _category_of_range(code)
    )
    assert not violations, (
        f"category does not match numeric range: {violations}. "
        "RANGE_EXEMPT is shrink-only -- fix the category, do not widen the exemption."
    )


def test_expect_error_code_directives_are_registered():
    """A typo'd EXPECT_ERROR_CODE header would silently assert nothing."""
    directive = re.compile(r"#\s*EXPECT_ERROR_CODE:\s*([A-Z]{2}\d{4})")
    unknown: list[str] = []
    for path in TEST_ROOT.rglob("test_*.sushi"):
        for code in directive.findall(path.read_text(encoding="utf-8")):
            if code not in REGISTRY:
                unknown.append(f"{path.name}: {code}")
    assert not unknown, f"EXPECT_ERROR_CODE names an unregistered code: {unknown}"


# Registered but referenced from nowhere in sushi_lang/. What remains is the "Group 3"
# set: codes reserved for genuinely-missing checks not yet implemented (plus CE0074, an
# unreachable internal guard). PR4 deleted the 17 speculative catch-alls that nothing
# would ever emit. This is an EXACT-MATCH ratchet: a new code may not join the list, and
# one that leaves must be removed from it.
# Tier 6.0 shrank it: CE0119 is now emitted (malformed expand, three shapes) and
# CE4008/CE4009 were deleted outright (CE4010 rejects generic perks at declaration,
# making implementation-arity codes unreachable by construction).
# TODO: implement each remaining check (CE1004 loop-shadow, CE2042/CE2043
# match exhaustiveness/pattern-type, CE3004 unit-path) and drop it from here;
# delete CE0074 if it stays unreachable.
UNREFERENCED = {
    "CE0074", "CE1004", "CE2042", "CE2043", "CE3004",
}


def test_unreferenced_codes_match_the_allowlist():
    """A registered code nothing can emit is dead weight -- track it, shrink it."""
    unreferenced = set(REGISTRY) - _referenced_codes()

    newly_dead = sorted(unreferenced - UNREFERENCED)
    assert not newly_dead, (
        f"these codes are registered but nothing emits them: {newly_dead}. "
        "Either emit them or do not register them."
    )

    revived = sorted(UNREFERENCED - unreferenced)
    assert not revived, (
        f"these codes are now referenced: {revived}. Remove them from UNREFERENCED."
    )


def test_runtime_texts_use_printf_or_braces_but_never_both():
    """An RE text is baked into the binary as a C string.

    For emit_runtime_error_with_values the registry text IS the printf format, so
    its % conversions must match the values the call site passes. For
    emit_runtime_error it may carry {} placeholders, formatted at codegen time.
    Mixing the two would either eat a conversion or print a literal brace.
    """
    for code, msg in REGISTRY.items():
        if not code.startswith("RE"):
            continue
        conversions = re.findall(r"%[-#0 +]*\d*(?:\.\d+)?[diouxXeEfgGcsp]", msg.text)
        placeholders = re.findall(r"\{(\w+)\}", msg.text)
        assert not (conversions and placeholders), (
            f"{code} mixes printf conversions {conversions} with placeholders "
            f"{placeholders}: {msg.text!r}"
        )


def test_re2020_format_matches_its_call_site():
    """RE2020's text is the format string the bounds check feeds two i32s to."""
    text = REGISTRY["RE2020"].text
    assert re.findall(r"%[a-zA-Z]", text) == ["%d", "%d"], (
        f"RE2020's text must take exactly two %d (index, size): {text!r}"
    )


def test_every_registered_code_has_a_severity_and_category():
    for code, msg in REGISTRY.items():
        assert isinstance(msg.severity, Severity), code
        assert isinstance(msg.category, Category), code
        assert msg.text, code
