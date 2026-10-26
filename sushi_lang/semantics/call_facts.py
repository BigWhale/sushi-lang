"""What the stop site of a `Call` knows, so that the label of the stop separates the groups.

The protocol is here, and not in `semantics/shir/`, because it is no SHIR class: it holds no
node data. `shir/dispatch.py:construct_of` reads it. `passes/types/call_facts.py` answers it.
"""
from __future__ import annotations

from typing import Optional, Protocol

from sushi_lang.semantics.shir.ids import DeclId


class CallFacts(Protocol):
    """What the stop site of a `Call` knows, so that the label separates the groups.

    The typecheck pass answers it from its declaration names and its tables. It decides
    nothing: `construct_of` is the one place that turns the facts into a label.
    """

    def written_name(self, decl: DeclId) -> str:
        """The written name of the declaration that a `GlobalRef` names."""

    def declaring_units(self, name: str) -> frozenset[Optional[str]]:
        """The units that declare an extension method or a perk method with this name."""

    def namespace_kind(self, alias: str, name: str) -> Optional[str]:
        """The kind of the namespace behind `alias` that holds `name`, or None."""

    def is_generic_function(self, name: str) -> bool:
        """Whether a bare `name` calls a generic function."""

    def is_stdlib_function(self, name: str) -> bool:
        """Whether a bare `name` calls a stdlib function."""

    def is_extern_function(self, name: str) -> bool:
        """Whether a bare `name` calls a C function with no namespace."""
