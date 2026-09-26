"""A user-declared struct is an LLVM *identified* type, not a literal one (#257)."""
from __future__ import annotations

from llvmlite import ir















def test_identified_type_is_set_body_not_recached():
    """The mechanism itself: set_body fills IN PLACE, so a mid-walk pointer stays valid."""
    module = ir.Module(name="pin")
    handle = module.context.get_identified_type("Tree")
    ptr_taken_before_body = ir.PointerType(handle)  # what a field walk would capture

    handle.set_body(ir.IntType(32), ir.LiteralStructType(
        [ir.IntType(32), ir.IntType(32), ir.PointerType(handle)]
    ))

    assert not handle.is_opaque
    assert ptr_taken_before_body == ir.PointerType(module.context.get_identified_type("Tree"))
    assert '%"Tree" = type {i32, {i32, i32, %"Tree"*}}' in str(module)
