"""One check over a struct construction's field arguments, two codes (#745).

The named constructor and the positional one held the same 48 lines: the field-type
resolution, the two propagation calls, the generic-callee rename and the compatibility
test. Only the code differs -- CE2083 for a named construction, CE2028 for a positional
one -- and the two texts are the same sentence.

The rows below are what the one loop has to keep answering, and the last one is what
stops the loop being written twice again.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.semantics.passes.types.calls import structs as structs_module


# The variable is USED in every row, so no CW1001 rides along and each row reads
# the whole diagnostic list.
















def test_the_field_argument_loop_is_written_once():
    """The gate: one loop, one resolution, one interning site, whatever the code is.

    The interning MOVED in #755: a written wrapper is interned through
    `utils.intern_declared_wrapper` for every position that writes one, so this file
    names the seam once and the `ensure_*` call not at all. The row is unchanged --
    one interning site -- and the spelling it counts follows the site.
    """
    source = Path(structs_module.__file__).read_text(encoding="utf-8")
    assert source.count("types_compatible(") == 1, "the compatibility test is written twice"
    assert source.count("intern_declared_wrapper(validator") == 1, "the interning is written twice"
    assert source.count("ensure_result_type_in_table(") == 0, "the intern seam is bypassed"
    assert source.count("propagate_types_to_value(validator") == 1
