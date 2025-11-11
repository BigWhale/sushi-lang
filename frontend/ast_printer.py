from __future__ import annotations
from dataclasses import is_dataclass, fields
from typing import Any

def _pp(node: Any, indent: int) -> str:
    ind = "  " * indent
    if isinstance(node, list):
        return "\n".join(_pp(n, indent) for n in node)
    if not is_dataclass(node):
        return ind + repr(node)
    name = node.__class__.__name__
    lines = [f"{ind}{name}"]
    for f in fields(node):
        if f.name == "loc":
            continue
        val = getattr(node, f.name)
        if is_dataclass(val) or isinstance(val, list):
            lines.append(f"{ind}  {f.name}:")
            lines.append(_pp(val, indent + 2))
        else:
            lines.append(f"{ind}  {f.name}: {val!r}")
    return "\n".join(lines)

def dump_ast(node: Any) -> str:
    return _pp(node, 0)

