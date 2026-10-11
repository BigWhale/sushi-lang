"""SHIR, the Sushi High-level IR: the input of the per-body passes (docs/design/ir.md 7).

Data only. Every class is slotted (gate: `tests/unit/test_shir_nodes_are_slotted.py`), and
no class carries a computed fact: a pass writes its answers into its own side table.
"""
