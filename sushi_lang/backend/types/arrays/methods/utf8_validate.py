"""
UTF-8 well-formedness validation for the checked byte-array -> string conversion.

Emits a single cached, module-internal function

    i1 sushi_utf8_validate(i8* data, i32 len)

that returns 1 iff `data[0..len)` is well-formed UTF-8 per the Unicode standard's
Table 3-7. It rejects overlong encodings, UTF-16 surrogate code points
(U+D800..U+DFFF), and code points above U+10FFFF -- not merely continuation-byte
shape. Used by u8[].to_string_checked() (see transforms.py).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import llvmlite.ir as ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

_VALIDATE_FN_NAME = "sushi_utf8_validate"


def get_or_emit_utf8_validate(codegen: "LLVMCodegen") -> ir.Function:
    """Get or emit the `i1 sushi_utf8_validate(i8* data, i32 len)` helper.

    The function is emitted once per module (cached via module globals) and reused
    by every to_string_checked() call site.
    """
    module = codegen.module
    existing = module.globals.get(_VALIDATE_FN_NAME)
    if isinstance(existing, ir.Function):
        return existing

    i1 = ir.IntType(1)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8p = ir.PointerType(i8)

    fn = ir.Function(module, ir.FunctionType(i1, [i8p, i32]), name=_VALIDATE_FN_NAME)
    fn.linkage = "internal"
    data, length = fn.args
    data.name, length.name = "data", "len"

    b = ir.IRBuilder(fn.append_basic_block("entry"))
    idx = b.alloca(i32, name="i")
    b.store(ir.Constant(i32, 0), idx)

    head = fn.append_basic_block("head")
    body = fn.append_basic_block("body")
    ret_true = fn.append_basic_block("ret_true")
    ret_false = fn.append_basic_block("ret_false")
    b.branch(head)

    # head: continue while i < len
    b.position_at_end(head)
    i_head = b.load(idx)
    b.cbranch(b.icmp_unsigned("<", i_head, length), body, ret_true)

    # body: classify the lead byte
    b.position_at_end(body)
    i_cur = b.load(idx)
    b0 = b.load(b.gep(data, [i_cur]), name="b0")

    ascii_bb = fn.append_basic_block("ascii")
    multi_bb = fn.append_basic_block("multi")
    b.cbranch(b.icmp_unsigned("<", b0, ir.Constant(i8, 0x80)), ascii_bb, multi_bb)

    # ASCII (0x00..0x7F): advance one byte
    b.position_at_end(ascii_bb)
    b.store(b.add(i_cur, ir.Constant(i32, 1)), idx)
    b.branch(head)

    # Multi-byte lead: valid leads are 0xC2..0xF4 (rejects continuation bytes as
    # leads, the overlong 0xC0/0xC1, and 0xF5..0xFF).
    b.position_at_end(multi_bb)
    bad_lead = b.or_(
        b.icmp_unsigned("<", b0, ir.Constant(i8, 0xC2)),
        b.icmp_unsigned(">", b0, ir.Constant(i8, 0xF4)),
    )
    ok_lead_bb = fn.append_basic_block("ok_lead")
    b.cbranch(bad_lead, ret_false, ok_lead_bb)

    b.position_at_end(ok_lead_bb)
    # Number of trailing continuation bytes: 1 (0xC2..0xDF), 2 (0xE0..0xEF), 3 (0xF0..0xF4).
    need = b.select(
        b.icmp_unsigned("<", b0, ir.Constant(i8, 0xE0)), ir.Constant(i32, 1),
        b.select(b.icmp_unsigned("<", b0, ir.Constant(i8, 0xF0)),
                 ir.Constant(i32, 2), ir.Constant(i32, 3)),
    )
    # Allowed range for the FIRST continuation byte encodes the overlong/surrogate/
    # range constraints (Table 3-7); all later continuations are plain 0x80..0xBF.
    lo2 = b.select(b.icmp_unsigned("==", b0, ir.Constant(i8, 0xE0)), ir.Constant(i8, 0xA0),
                   b.select(b.icmp_unsigned("==", b0, ir.Constant(i8, 0xF0)),
                            ir.Constant(i8, 0x90), ir.Constant(i8, 0x80)))
    hi2 = b.select(b.icmp_unsigned("==", b0, ir.Constant(i8, 0xED)), ir.Constant(i8, 0x9F),
                   b.select(b.icmp_unsigned("==", b0, ir.Constant(i8, 0xF4)),
                            ir.Constant(i8, 0x8F), ir.Constant(i8, 0xBF)))

    # Require `need` continuation bytes to exist: indices i+1..i+need must be < len.
    room = b.icmp_unsigned("<", b.add(i_cur, need), length)
    room_ok_bb = fn.append_basic_block("room_ok")
    b.cbranch(room, room_ok_bb, ret_false)

    b.position_at_end(room_ok_bb)
    c1 = b.load(b.gep(data, [b.add(i_cur, ir.Constant(i32, 1))]), name="c1")
    c1_ok = b.and_(b.icmp_unsigned(">=", c1, lo2), b.icmp_unsigned("<=", c1, hi2))
    cont_bb = fn.append_basic_block("cont_loop")
    b.cbranch(c1_ok, cont_bb, ret_false)

    # Remaining continuations j = 2..need: each must match 0b10xxxxxx.
    b.position_at_end(cont_bb)
    j = b.alloca(i32, name="j")
    b.store(ir.Constant(i32, 2), j)
    cl_head = fn.append_basic_block("cl_head")
    cl_body = fn.append_basic_block("cl_body")
    cl_next = fn.append_basic_block("cl_next")
    cl_done = fn.append_basic_block("cl_done")
    b.branch(cl_head)

    b.position_at_end(cl_head)
    j_cur = b.load(j)
    b.cbranch(b.icmp_unsigned("<=", j_cur, need), cl_body, cl_done)

    b.position_at_end(cl_body)
    ck = b.load(b.gep(data, [b.add(i_cur, j_cur)]), name="ck")
    is_cont = b.icmp_unsigned("==", b.and_(ck, ir.Constant(i8, 0xC0)), ir.Constant(i8, 0x80))
    b.cbranch(is_cont, cl_next, ret_false)

    b.position_at_end(cl_next)
    b.store(b.add(j_cur, ir.Constant(i32, 1)), j)
    b.branch(cl_head)

    # Whole sequence valid: advance past lead + continuations.
    b.position_at_end(cl_done)
    b.store(b.add(i_cur, b.add(need, ir.Constant(i32, 1))), idx)
    b.branch(head)

    b.position_at_end(ret_true)
    b.ret(ir.Constant(i1, 1))
    b.position_at_end(ret_false)
    b.ret(ir.Constant(i1, 0))

    return fn
