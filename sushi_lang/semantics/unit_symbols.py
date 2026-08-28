"""The link symbol a unit's declaration takes.

One rule, read by the back end when it declares a function and by the `.slib` producer
when it records `link_symbol`. Two units may each declare `helper`, so a symbol has to
say which unit's declaration it is (`docs/design/unit-namespaces.md` section 9).
"""
from __future__ import annotations

from typing import Optional

# `$` lies OUTSIDE the alphabet of every other symbol component -- an identifier and a
# sanitized type argument are [A-Za-z0-9_], and the pack marker's separator is "." -- so
# a unit prefix cannot occur in an unprefixed symbol. LLVM accepts it in an identifier.
UNIT_SEP = "$"

# The C entry point. The linker needs the name, and the `entrypoint` pass already
# guarantees one program declares one `main`, so there is nothing to disambiguate.
EXEMPT = frozenset({"main"})


def mangle_unit_symbol(unit_name: Optional[str], name: str) -> str:
    """`<unit>$<name>`, with every `/` in the unit name becoming `$`.

    No unit means no prefix. A monomorphized instance, a lifted lambda and a generated
    stdlib symbol are program-wide, and each already carries a name nothing else can
    take.
    """
    if unit_name is None or name in EXEMPT:
        return name
    return f"{unit_name.replace('/', UNIT_SEP)}{UNIT_SEP}{name}"
