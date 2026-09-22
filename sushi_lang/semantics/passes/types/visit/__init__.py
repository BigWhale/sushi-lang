"""The typecheck pass's three visitors, one module each.

A statement is validated by `statements.py`, an expression by `expressions.py`, and
what an expression YIELDS is answered by `inference.py`. The helpers the three share
are `helpers.py`, and the dot-call ladder the last two share is `calls/dotcall.py`.
"""
