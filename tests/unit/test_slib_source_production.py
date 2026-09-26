"""`--lib-kind source` ships the library's units as text.

Production only: nothing consumes a source library yet. What is asserted here is what
lands in the container, and that a source library stops being platform-bound.
See `docs/design/libraries.md` section 4.1.
"""
from __future__ import annotations


import pytest

from sushi_lang.backend.library_errors import LibraryError








# --- What a source library contains -----------------------------------------















# --- The other two kinds ----------------------------------------------------







# --- A source library is not platform-bound ---------------------------------

def _load(metadata, kind):
    from sushi_lang.compiler.pipeline import _check_library_platform

    _check_library_platform({**metadata, "kind": kind}, "lib/x")


FOREIGN = {"library_name": "x", "platform": "definitely-not-this-one"}


def test_a_source_library_loads_on_any_platform():
    # The single conditional that A3 was really asking for: the platform field is
    # meaningless when nothing in the container is machine code.
    _load(FOREIGN, "source")


@pytest.mark.parametrize("kind", ["binary", "hybrid"])
def test_a_library_carrying_bitcode_is_still_platform_bound(kind):
    with pytest.raises(LibraryError) as excinfo:
        _load(FOREIGN, kind)
    assert excinfo.value.code == "CE3504"


def test_a_matching_platform_loads_whatever_the_kind():
    from sushi_lang.backend.platform_detect import current_platform_name

    for kind in ("source", "binary", "hybrid"):
        _load({"library_name": "x", "platform": current_platform_name()}, kind)
