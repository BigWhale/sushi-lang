"""SLIR, the Sushi Low-level IR: the input of code generation (docs/design/ir.md 8).

A control-flow graph of basic blocks over places and locals. The node modules are data
only, and every class is a slotted dataclass (gate: `tests/unit/test_ir_nodes_are_slotted.py`).
Every statement and every terminator carries a required span (G9). `lower.py` makes a
`SlirFunction` from a SHIR body that `borrow` finished, and `printer.py` gives the text of
`--dump-slir`.
"""
