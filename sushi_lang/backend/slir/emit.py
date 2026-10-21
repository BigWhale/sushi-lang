"""One `SlirFunction` into the LLVM function that the declaration round made for its symbol.

Every local is a slot in the entry block (`entry_alloca`, the one seam), except a `peek`
or a `poke` parameter: its local IS the incoming address (docs/design/ir.md 8.3). A value
parameter is stored into its slot at the entry. `_0` is the return slot, and `return`
loads it. Each SLIR block is one LLVM block, in the order of the function. A statement
class or a terminator class that the emitter does not handle yet stops the compile
(`emit, <class>`). `StorageLive` and `StorageDead` emit nothing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.slir.values import (
    type_of_constant, constant_value, operand_value, place_address, rvalue_value,
)
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirFunction, SlirLocal
from sushi_lang.semantics.slir.lower import RETURN_SLOT

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_function(codegen: LLVMCodegen, function: SlirFunction, *,
                  filename: Optional[str]) -> ir.Function:
    """Define the LLVM function of `function`, and answer it.

    The stage entry of the emitter: no pytest test may reach it (`compiler_stage_gate`).
    """
    llvm_fn = codegen.module.globals.get(function.symbol)
    if not isinstance(llvm_fn, ir.Function) or not llvm_fn.is_declaration:
        raise_internal_error("CE0000", filename=filename,
                             detail=f"the module holds no declaration of "
                                    f"'{function.symbol}' to define")
    entry = llvm_fn.append_basic_block(name="entry")
    state = FunctionState(function, llvm_fn, ir.IRBuilder(entry), filename)
    state.blocks = {block.id: llvm_fn.append_basic_block(name=f"bb{block.id.index}")
                    for block in function.blocks}
    saved = codegen.builder, codegen.func
    codegen.builder, codegen.func = state.builder, llvm_fn
    try:
        _open(codegen, state)
        state.builder.branch(state.block(function.entry))
        for block in function.blocks:
            state.builder.position_at_end(state.block(block.id))
            for statement in block.statements:
                state.span = statement.span
                _statement(codegen, state, statement)
            state.span = block.terminator.span
            _terminator(codegen, state, block.terminator)
    finally:
        codegen.builder, codegen.func = saved
    return llvm_fn


def _open(codegen: LLVMCodegen, state: FunctionState) -> None:
    """The storage of each local, and the value parameters stored into their slots. The
    signature that the declaration made must agree with the locals and the conventions."""
    function, llvm_fn = state.function, state.llvm_fn
    if (len(llvm_fn.args) != len(function.params)
            or llvm_fn.function_type.return_type != _ll(codegen, function.locals[0])):
        state.fault(f"the declaration {llvm_fn.function_type} does not agree with the "
                    f"SLIR header")
    params = {local: (index, convention)
              for index, (local, convention) in enumerate(function.params)}
    for local in function.locals:
        ll = _ll(codegen, local)
        param = params.get(local.id)
        if param is None:
            state.storage[local.id] = codegen.memory.entry_alloca(ll, _name(local))
            continue
        arg = llvm_fn.args[param[0]]
        by_address = param[1].by_address
        if arg.type != (ll.as_pointer() if by_address else ll):
            state.fault(f"the parameter _{local.id.index} is {arg.type} in the declaration")
        if by_address:
            state.storage[local.id] = arg
            continue
        slot = codegen.memory.entry_alloca(ll, _name(local))
        state.builder.store(arg, slot)
        state.storage[local.id] = slot


def _ll(codegen: LLVMCodegen, local: SlirLocal) -> ir.Type:
    return codegen.types.ll_type(local.ty)


def _name(local: SlirLocal) -> str:
    return local.name or f"_{local.id.index}"


# -- statements ------------------------------------------------------------------------

def _statement(codegen: LLVMCodegen, state: FunctionState, statement: st.SlirStatement) -> None:
    """`StorageLive` and `StorageDead` emit nothing: the emitter keeps every local in one
    slot for the whole function."""
    if isinstance(statement, st.Assign):
        value = rvalue_value(codegen, state, statement.rvalue)
        state.builder.store(value, place_address(state, statement.place))
    elif isinstance(statement, st.Call):
        _call(codegen, state, statement)
    elif not isinstance(statement, (st.StorageLive, st.StorageDead)):
        state.stop(statement)


def _call(codegen: LLVMCodegen, state: FunctionState, statement: st.Call) -> None:
    """A `Direct` call: a value for an `Owned` or a `Guaranteed` argument, the address of
    the place for an `InGuaranteed` or an `Inout` one. Another callee stops."""
    callee = statement.callee
    if not isinstance(callee, st.Direct):
        state.stop(callee)
    llvm_fn = codegen.module.globals.get(callee.symbol)
    if not isinstance(llvm_fn, ir.Function):
        state.fault(f"the module declares no function '{callee.symbol}'")
    args = [_argument(codegen, state, operand, convention)
            for operand, convention in statement.args]
    if [arg.type for arg in args] != [param.type for param in llvm_fn.args]:
        state.fault(f"the call of '{callee.symbol}' does not agree with its declaration "
                    f"{llvm_fn.function_type}")
    value = state.builder.call(llvm_fn, args)
    state.builder.store(value, place_address(state, statement.dest))


def _argument(codegen: LLVMCodegen, state: FunctionState, operand: p.Operand,
              convention: Convention) -> ir.Value:
    if not convention.by_address:
        return operand_value(codegen, state, operand)
    if isinstance(operand, p.Copy):
        return place_address(state, operand.place)
    if isinstance(operand, p.Const):
        value = constant_value(codegen, state, operand.constant)
        slot = codegen.memory.entry_alloca(
            codegen.types.ll_type(type_of_constant(state, operand.constant)), "arg")
        state.builder.store(value, slot)
        return slot
    state.fault(f"a {convention.value} argument is a move")


# -- terminators -----------------------------------------------------------------------

def _terminator(codegen: LLVMCodegen, state: FunctionState,
                terminator: tm.SlirTerminator) -> None:
    builder = state.builder
    if isinstance(terminator, tm.Goto):
        builder.branch(state.block(terminator.target))
    elif isinstance(terminator, tm.SwitchInt):
        _switch(codegen, state, terminator)
    elif isinstance(terminator, tm.Return):
        builder.ret(builder.load(state.storage_of(RETURN_SLOT)))
    elif isinstance(terminator, tm.Unreachable):
        builder.unreachable()
    else:
        state.stop(terminator)


def _switch(codegen: LLVMCodegen, state: FunctionState, terminator: tm.SwitchInt) -> None:
    """`otherwise: None` goes to a block of its own that holds `unreachable`."""
    value = operand_value(codegen, state, terminator.operand)
    if terminator.otherwise is None:
        default = state.llvm_fn.append_basic_block(name="unreachable")
        ir.IRBuilder(default).unreachable()
    else:
        default = state.block(terminator.otherwise)
    switch = state.builder.switch(value, default)
    for case, target in terminator.targets:
        switch.add_case(ir.Constant(value.type, case), state.block(target))
