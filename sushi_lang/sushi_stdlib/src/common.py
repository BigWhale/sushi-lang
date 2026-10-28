"""Common utilities and infrastructure for built-in extension methods."""

from typing import Optional, Callable, Any, Sequence
from dataclasses import dataclass
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.derived_methods import builtin_registry
import llvmlite.ir as ir


@dataclass
class BuiltinMethod:
    """Metadata for a built-in extension method."""
    name: str
    return_type: Optional[Type]
    description: str
    llvm_emitter: Callable[[Any, Sequence[ir.Value], ir.Value, ir.Type], ir.Value]


def register_builtin_method(target_type: Type, method: BuiltinMethod) -> None:
    """Register a method the compiler defines for every program.

    The primitive families only. A method the `derive` pass writes for a type a PROGRAM
    declares goes in that compilation's own table (#601) -- see
    `semantics/derived_methods.py`.
    """
    builtin_registry.register_method(target_type, method)

