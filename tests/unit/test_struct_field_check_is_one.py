"""One check over a struct construction's field arguments, two codes (#745).

The named constructor and the positional one held the same 48 lines: the field-type
resolution, the two propagation calls, the generic-callee rename and the compatibility
test. Only the code differs -- CE2083 for a named construction, CE2028 for a positional
one -- and the two texts are the same sentence.

Since group A5 the fields of a `StructInit` go through the one argument check
(`arguments.check_arguments`), with the code of the construction, so the struct module
holds no loop of its own. The rows below are what stops the loop being written in the
module again.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.semantics.passes.types.calls import structs as structs_module


def test_the_field_arguments_go_through_the_one_argument_check():
    """The gate: one call of the seam, one resolution, one interning site.

    The interning MOVED in #755: a written wrapper is interned through
    `utils.intern_declared_wrapper` for every position that writes one, so this file
    names the seam once and the `ensure_*` call not at all.
    """
    source = Path(structs_module.__file__).read_text(encoding="utf-8")
    assert source.count("check_arguments(validator") == 1, "the argument check is not one"
    assert source.count("types_compatible(") == 0, "a second compatibility test is written"
    assert source.count("propagate_types_to_value(") == 0, "an AST propagation is back"
    assert source.count("intern_declared_wrapper(validator") == 1, "the interning is written twice"
    assert source.count("ensure_result_type_in_table(") == 0, "the intern seam is bypassed"
