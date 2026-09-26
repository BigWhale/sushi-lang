"""An operation the compiler reads computes at the declared width (Ruling 1, #446).

The evaluator held a Python integer of unlimited size and marked the exact result with
the type of the left operand, so a constant could hold a value its type cannot: `200 +
100` on a u8 held 300. Truncation hid it for `+`, `-`, `*`, `<<` and `~`, and exposed it
for `/`, `%`, `>>`, a comparison, a widening cast, an array index and an array size --
each of those reads the held value, so each disagreed with a body.

Two rules answer it, and the table below asks the question per operator so a set that
loses a member cannot pass:

- an OVERFLOW-CHECKED operator (`+ - * / %`, unary `-`) reports CE2077;
- a WIDTH-DEFINED operator (`~ & | ^ << >>`) computes at the width and never reports.

`docs/design/compile-time-evaluation.md` is normative.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.integer_width import fits_integer_type
from sushi_lang.semantics.typesys import BuiltinType








# One expression per checked operator, valued so only the operation leaves the type.

# One expression per width-defined operator, with a result the width alone decides.






















@pytest.mark.parametrize("ty,value,fits", [
    (BuiltinType.U8, 255, True),
    (BuiltinType.U8, 256, False),
    (BuiltinType.U8, -1, False),
    (BuiltinType.I8, 127, True),
    (BuiltinType.I8, 128, False),
    (BuiltinType.I8, -128, True),
    (BuiltinType.I8, -129, False),
    (BuiltinType.U64, 18446744073709551615, True),
    (BuiltinType.U64, 18446744073709551616, False),
    (BuiltinType.I64, -9223372036854775808, True),
    (BuiltinType.I64, 9223372036854775808, False),
    (BuiltinType.F64, 1, False),
])
def test_the_range_of_an_integer_type(ty, value, fits):
    """One table answers the range question, for the checker and for the literals."""
    assert fits_integer_type(value, ty) is fits


