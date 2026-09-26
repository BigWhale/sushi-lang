"""One answer to what a WRITTEN type names (#755).

The typecheck pass asked the same question in five places, and every one of them ended at
`semantics/type_resolution` by a route of its own. Two questions hide in those five:

* **what does this spelling NAME?** -- a lookup, and the table is its authority.
  `utils.resolve_declared_type` answers it, over `resolve_unknown_type` for a name and
  `resolve_type_recursively` for a type that CONTAINS one.
* **what does this spelling name, INTERNED?** -- a written `Result@(T, E)` or `Maybe@(T)`
  can name an entry nobody has built yet, so it goes through the one seam that may build
  one. `utils.intern_declared_wrapper` answers it, and a lookup answers the rest.

The routing rows below are what stops a sixth answer being written. The characterization
rows under them PIN the answer each site gives today: this ticket promises no behaviour
change, so each of them reads the same before the refactor and after it.
"""
from __future__ import annotations

from pathlib import Path


_PASS_DIR = Path(__file__).resolve().parents[2] / "sushi_lang/semantics/passes/types"




# --------------------------------------------------------------------------------------
# The routing: every WRITTEN wrapper is interned through one function.
# --------------------------------------------------------------------------------------





def test_the_seam_answers_none_for_everything_that_is_not_a_written_wrapper():
    """The control: the seam is a FILTER, so a caller can tell a miss from an answer."""
    from sushi_lang.semantics.passes.types.utils import intern_declared_wrapper
    from sushi_lang.semantics.typesys import BuiltinType

    assert intern_declared_wrapper(None, BuiltinType.I32) is None
    assert intern_declared_wrapper(None, None) is None


def test_the_pass_holds_no_type_resolver_of_its_own():
    """`TypeResolver` is a wrapper over two functions the pass already calls directly."""
    scanned = sorted(_PASS_DIR.rglob("*.py"))
    assert len(scanned) > 30, "the scan found no pass modules -- it would pass vacuously"

    holders = [p.relative_to(_PASS_DIR).as_posix()
               for p in scanned if "TypeResolver" in p.read_text(encoding="utf-8")]
    assert holders == []


def test_the_type_resolver_name_is_still_findable():
    """The control for the row above: the spelling it looks for still exists."""
    home = _PASS_DIR.parents[1] / "type_resolution.py"
    assert "class TypeResolver" in home.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# The characterization: what each home answers today, read off the diagnostic that
# renders the resolved type.
# --------------------------------------------------------------------------------------





















