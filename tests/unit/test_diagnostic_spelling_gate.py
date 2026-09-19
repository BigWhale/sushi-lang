"""The spelling gate: an interned type name must not reach a diagnostic (#734).

Source syntax is `@(...)`. The angle-bracket form is the INTERNAL identity name and a
table key, and `display_type()` is the one renderer that turns it back. One renderer
answers for many emit sites, so the gate is what asks the emit sites.

`test_diagnostics_use_display_type.py` reads the SOURCE for the same rule. It reads a
keyword value that is an f-string or a `str(...)` call, so a bare `struct_type.name`
went past it, and a note built in a pass is not a keyword value at all. This gate reads
the RENDERED text instead, which is the one thing every emit site has in common.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import (
    SPELLING_GATE_ENV,
    DiagnosticSpellingError,
    Reporter,
    Span,
    check_spelling,
    spelling_gate_is_on,
)

# Text that carries an interned name. Every one of these is a fault.
REFUSED = [
    "type List<i32> already implements perk Show",
    "enum variant 'Nope' not found in enum 'Maybe<i32>'",
    "struct 'Pair<i32, string>' expects 2 field(s), got 1",
    "unknown field 'nope' for struct 'Pair<i32, string>'",
    "declare 'extend Maybe<i32> static Nope(...)'",
]

# Text with a legitimate `<`. Each one is preceded by a quote, a space or a line
# start, which is what makes the one regex enough. Measured over the whole fixture
# corpus: 4 hits in 1338 diagnostic lines, and no false positive.
ACCEPTED = [
    "operator '<' expects a numeric operand",
    "the shift operator '<<' needs a readable count",
    "expected <value>, found a type name",
    "cannot read <expression> here",
    "a string method needs 'use <collections/strings>'",
    "<input>:1:1 has no source",
    "type 'fn(i32) -> i32' has no hash",
]


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(SPELLING_GATE_ENV, "1")


@pytest.fixture
def disarmed(monkeypatch):
    monkeypatch.delenv(SPELLING_GATE_ENV, raising=False)


@pytest.mark.parametrize("text", REFUSED)
def test_the_gate_refuses_an_interned_name(armed, text):
    with pytest.raises(DiagnosticSpellingError):
        check_spelling("CE0000", text)


@pytest.mark.parametrize("text", ACCEPTED)
def test_the_gate_admits_a_legitimate_angle_bracket(armed, text):
    check_spelling("CE0000", text)


def test_the_gate_is_off_by_default(disarmed):
    """A cosmetic fault is never worth a user-facing crash."""
    assert not spelling_gate_is_on()
    check_spelling("CE4002", "type List<i32> already implements perk Show")


@pytest.mark.parametrize("value,on", [
    ("1", True), ("yes", True), ("on", True),
    ("", False), ("0", False), ("off", False), ("OFF", False),
])
def test_the_environment_arms_the_gate(monkeypatch, value, on):
    monkeypatch.setenv(SPELLING_GATE_ENV, value)
    assert spelling_gate_is_on() is on


def test_the_message_of_a_recorded_diagnostic_is_asked(armed):
    r = Reporter(source="x", filename="t.sushi")
    with pytest.raises(DiagnosticSpellingError):
        r.error("CE4002", "type List<i32> already implements perk Show",
                Span(1, 1, 1, 2))


def test_a_note_and_a_help_are_asked_too(armed):
    """They are attached after the diagnostic is recorded, so they need their own ask."""
    r = Reporter(source="x", filename="t.sushi")
    with pytest.raises(DiagnosticSpellingError):
        r.error_with("CE4002", "fine", Span(1, 1, 1, 2)).note("from Pair<i32, string>")

    r = Reporter(source="x", filename="t.sushi")
    with pytest.raises(DiagnosticSpellingError):
        r.error_with("CE4002", "fine", Span(1, 1, 1, 2)).help("write Maybe<i32>")


def test_the_message_names_the_gate(armed):
    """The runners read stderr for this prefix, so an exit code cannot hide a trip."""
    with pytest.raises(DiagnosticSpellingError) as caught:
        check_spelling("CE4002", "List<i32>")
    assert str(caught.value).startswith(f"{SPELLING_GATE_ENV}:")


def test_the_gate_is_not_catchable_as_an_ordinary_error(armed):
    """The compiler renders CE0000 from an `Exception`, which would hide the emit site."""
    assert not issubclass(DiagnosticSpellingError, Exception)
    try:
        check_spelling("CE4002", "List<i32>")
    except Exception:  # noqa: B902 - the point of the test
        pytest.fail("the spelling gate was swallowed by an `except Exception`")
    except DiagnosticSpellingError:
        pass
