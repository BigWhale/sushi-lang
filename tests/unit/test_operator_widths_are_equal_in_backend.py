"""The backend compares and combines integer operands of ONE width, or it stops (#840).

The typecheck pass refuses a mixed width for `== != < <= > >=` and for `& | ^` (CE2510),
and `_emit_shift` brings a shift count to the value's width itself. So the comparison
and bitwise emitters never receive two integer operands of different widths. They used
to reconcile the widths anyway: the comparison squeezed both operands to i32 and
compared them signed (an i64 truncated, an unsigned operand took the wrong sign), and
the bitwise emitter extended or truncated the right operand. A mixed pair that a future
typecheck change lets through would have compiled to a wrong value with no diagnostic.
Now it is the internal error CE0139.
"""
from __future__ import annotations


from sushi_lang.backend.expressions import operators

















def test_the_i32_squeeze_is_gone():
    assert not hasattr(operators, "_ensure_i32")
