"""The `!` return marker (docs/design/never-return.md).

`!` is a marker and not a type: `BuiltinType.NEVER` stands only in the return slot of a
free function or an extern, as the AST builder reads it (`read_return_type`). The declared
return is the one home of the rule. A reader asks `never_returns` whether a call of a
callable ends the path, and asks `returned_value` for the type of what the call gives.
"""
from __future__ import annotations

from typing import Any, Optional, overload

from sushi_lang.semantics.channel import declared_return
from sushi_lang.semantics.typesys import BuiltinType, Type


def never_returns(declared: Any) -> bool:
    """Whether a call of `declared` (a declaration or a signature record) never returns."""
    return declared_return(declared) == BuiltinType.NEVER


@overload
def returned_value(ret: Type) -> Type: ...
@overload
def returned_value(ret: Optional[Type]) -> Optional[Type]: ...
def returned_value(ret: Optional[Type]) -> Optional[Type]:
    """The type of the value that a return slot of `ret` holds: `~` for `!`, because no
    value has the marker."""
    return BuiltinType.BLANK if ret == BuiltinType.NEVER else ret
