"""The emitter of a `SlirFunction`: SLIR to LLVM IR (docs/design/ir.md 8.2 to 8.8).

The package follows the backend rule: free functions, and one state object for each
function (`FunctionState`: the codegen, the storage of each local and the LLVM block of
each SLIR block). The stage entry takes `codegen` first and makes the state; every other
function takes the state first and reads the codegen from it. `bodies.py` is the hand-off
of the SLIR of the program to the backend, `emit.py` the function, its statements and its
terminators, `values.py` the places, the operands, the constants and the rvalues, and
`intrinsics.py` the calls of an intrinsic.

A SLIR shape that the emitter does not handle yet stops the compile with the stop code
CE0155 (`emit, <SLIR class>`), at the span of its statement, before the pipeline writes
any output.
"""
