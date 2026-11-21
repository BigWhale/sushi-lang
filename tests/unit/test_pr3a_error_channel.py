"""PR3a: false ICEs become real diagnostics; the error channel loses its swallows."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# The bare-except sweep -- an acceptance criterion, made a permanent gate

def test_no_bare_except_in_sushi_lang():
    """A bare `except:` catches KeyboardInterrupt/SystemExit/MemoryError."""
    bare = re.compile(r"except\s*:")
    offenders = []
    for path in (REPO / "sushi_lang").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if bare.match(stripped):
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, f"bare `except:` found (narrow it): {offenders}"


# CE3510 / CE3511 -- truncation, raised with a LITERAL code so the gate sees it

def test_truncated_metadata_section_is_ce3510():
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import _read_bytes

    stream = io.BytesIO(b"short")
    with pytest.raises(LibraryError) as exc:
        _read_bytes(stream, 100, "lib.slib", "metadata")
    assert exc.value.code == "CE3510"


def test_truncated_bitcode_section_is_ce3511():
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import _read_bytes

    stream = io.BytesIO(b"short")
    with pytest.raises(LibraryError) as exc:
        _read_bytes(stream, 100, "lib.slib", "bitcode")
    assert exc.value.code == "CE3511"


def test_library_error_renders_through_the_reporter_path():
    """A LibraryError is a SushiError, so the top-level guard renders its own code."""
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.internals.diagnostics import SushiError

    err = LibraryError("CE3504", lib_platform="linux", current_platform="darwin")
    assert isinstance(err, SushiError)
    assert err.code == "CE3504"
    assert "platform mismatch" in err.message
