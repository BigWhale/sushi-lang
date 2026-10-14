"""SHIR, the Sushi High-level IR: the input of the per-body passes (docs/design/ir.md 7).

The node modules are data only. Every class is slotted (gate:
`tests/unit/test_shir_nodes_are_slotted.py`), and no class carries a computed fact: a pass
writes its answers into its own side table. `printer.py` gives the text of `--dump-shir`.
"""
