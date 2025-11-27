# semantics/passes/collect/perks.py
"""Perk definition and implementation collection for Phase 0."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from internals.report import Reporter, Span
from internals import errors as er
from internals.errors import ERR
from semantics.ast import PerkDef, ExtendWithDef, FuncDef, Program
from semantics.typesys import Type, BuiltinType, StructType, EnumType

from .utils import format_location


@dataclass
class PerkTable:
    """Registry of all defined perks."""
    by_name: Dict[str, PerkDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def register(self, perk: PerkDef) -> bool:
        """Register a perk. Returns False if duplicate."""
        if perk.name in self.by_name:
            return False
        self.by_name[perk.name] = perk
        self.order.append(perk.name)
        return True

    def get(self, name: str) -> Optional[PerkDef]:
        """Get a perk definition by name."""
        return self.by_name.get(name)


@dataclass
class PerkImplementationTable:
    """Tracks which types implement which perks."""
    # Key: (type_name, perk_name), Value: ExtendWithDef
    implementations: Dict[Tuple[str, str], ExtendWithDef] = field(default_factory=dict)

    # Reverse index: type_name -> set of implemented perk names
    by_type: Dict[str, Set[str]] = field(default_factory=dict)

    # Reverse index: perk_name -> set of implementing type names
    by_perk: Dict[str, Set[str]] = field(default_factory=dict)

    def register(self, impl: ExtendWithDef, type_name: str) -> bool:
        """Register an implementation. Returns False if duplicate."""
        key = (type_name, impl.perk_name)
        if key in self.implementations:
            return False  # Duplicate implementation

        self.implementations[key] = impl

        # Update indexes
        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(impl.perk_name)

        if impl.perk_name not in self.by_perk:
            self.by_perk[impl.perk_name] = set()
        self.by_perk[impl.perk_name].add(type_name)

        return True

    def implements(self, type_name: str, perk_name: str) -> bool:
        """Check if a type implements a perk."""
        return (type_name, perk_name) in self.implementations

    def get_implementations(self, type_name: str) -> Set[str]:
        """Get all perks implemented by a type."""
        return self.by_type.get(type_name, set())

    def get(self, type_name: str, perk_name: str) -> Optional[ExtendWithDef]:
        """Get a specific perk implementation."""
        return self.implementations.get((type_name, perk_name))

    def get_method(self, target_type: 'Type', method_name: str) -> Optional['FuncDef']:
        """Get a specific perk method for a type.

        Searches all perks implemented by the type to find the method.
        Returns the method definition if found, None otherwise.
        """
        # Convert Type to string name for lookup
        type_name = _get_type_name(target_type)
        if type_name is None:
            return None

        # Check all perks implemented by this type
        perks = self.by_type.get(type_name, set())
        for perk_name in perks:
            impl = self.implementations.get((type_name, perk_name))
            if impl:
                # Search for the method in this implementation
                for method in impl.methods:
                    if method.name == method_name:
                        return method

        return None

    def register_synthetic(self, type_name: str, perk_name: str) -> bool:
        """Register a synthetic perk implementation for primitives.

        Synthetic implementations are used when a primitive type has auto-derived
        methods that satisfy a perk's requirements, but no explicit 'extend...with'
        declaration exists.

        This allows primitives (i32, string, bool, etc.) to work seamlessly with
        generic constraints like T: Hashable.

        Args:
            type_name: Name of the primitive type (e.g., "i32", "string")
            perk_name: Name of the perk being implemented (e.g., "Hashable")

        Returns:
            True if registered successfully, False if already exists
        """
        key = (type_name, perk_name)
        if key in self.implementations:
            return False  # Already registered (explicit or synthetic)

        # Register as synthetic implementation (None indicates synthetic)
        self.implementations[key] = None  # type: ignore

        # Update indexes
        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(perk_name)

        if perk_name not in self.by_perk:
            self.by_perk[perk_name] = set()
        self.by_perk[perk_name].add(type_name)

        return True


def _get_type_name(ty: Optional[Type]) -> Optional[str]:
    """Extract a string name from a Type for use in perk implementation tables.

    Args:
        ty: Type to extract name from

    Returns:
        String name or None if type cannot be named
    """
    if ty is None:
        return None

    # Handle built-in types
    if isinstance(ty, BuiltinType):
        return str(ty)

    # Handle struct types
    if isinstance(ty, StructType):
        return ty.name

    # Handle enum types
    if isinstance(ty, EnumType):
        return ty.name

    # Handle generic type references (e.g., List<i32>)
    from semantics.generics.types import GenericTypeRef
    if isinstance(ty, GenericTypeRef):
        return f"{ty.base_name}<{','.join(str(arg) for arg in ty.type_args)}>"

    # Fallback to string representation
    return str(ty)


class PerkCollector:
    """Collector for perk definitions and implementations.

    Collects:
    - Perk definitions (interfaces)
    - Perk implementations (extend...with)
    - Synthetic perk implementations (auto-registered for primitives)

    Validates:
    - No duplicate perk names
    - No duplicate implementations for same type+perk pair
    - Referenced perks exist
    """

    def __init__(
        self,
        reporter: Reporter,
        perks: PerkTable,
        perk_impls: PerkImplementationTable
    ) -> None:
        """Initialize perk collector.

        Args:
            reporter: Error reporter
            perks: Shared perk table
            perk_impls: Shared perk implementation table
        """
        self.r = reporter
        self.perks = perks
        self.perk_impls = perk_impls

    def collect_definitions(self, root: Program) -> None:
        """Collect all perk definitions from program AST.

        Args:
            root: Program AST node
        """
        perks = getattr(root, "perks", None)
        if isinstance(perks, list):
            for perk in perks:
                if isinstance(perk, PerkDef):
                    self._collect_perk_def(perk)

    def collect_implementations(self, root: Program) -> None:
        """Collect all perk implementations from program AST.

        Args:
            root: Program AST node
        """
        perk_impls = getattr(root, "perk_impls", None)
        if isinstance(perk_impls, list):
            for impl in perk_impls:
                if isinstance(impl, ExtendWithDef):
                    self._collect_perk_impl(impl)

    def register_synthetic_impls(self) -> None:
        """Auto-register synthetic perk implementations for primitive types.

        This method checks which perks are defined and automatically registers
        primitives that have the required auto-derived methods.

        Currently supports:
        - Hashable perk: Registers all primitives with auto-derived hash() methods
          (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string)

        This allows primitives to work seamlessly with generic constraints
        (e.g., fn compute_hash<T: Hashable>(T value)) without requiring
        explicit 'extend i32 with Hashable' declarations.
        """
        # Primitives with auto-derived hash() methods
        # See: backend/types/primitives/hashing.py
        hashable_primitives = [
            "i8", "i16", "i32", "i64",
            "u8", "u16", "u32", "u64",
            "f32", "f64", "bool", "string"
        ]

        # Check if Hashable perk is defined
        hashable_perk = self.perks.get("Hashable")
        if hashable_perk:
            # Verify perk requires hash() method
            has_hash_method = any(
                method.name == "hash" and method.ret == BuiltinType.U64
                for method in hashable_perk.methods
            )

            if has_hash_method:
                # Register all hashable primitives
                for prim_type in hashable_primitives:
                    self.perk_impls.register_synthetic(prim_type, "Hashable")

    def _collect_perk_def(self, perk: PerkDef) -> None:
        """Collect perk definition and register in perk table.

        Args:
            perk: Perk definition AST node
        """
        name = getattr(perk, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(perk, "name_span", None) or getattr(perk, "loc", None)

        # Check for duplicate perk names
        if not self.perks.register(perk):
            prev = self.perks.get(name)
            prev_loc = format_location(self.r, getattr(prev, "name_span", None)) if prev else "unknown"
            er.emit(self.r, ERR.CE4001, name_span, name=name)
            return

    def _collect_perk_impl(self, impl: ExtendWithDef) -> None:
        """Collect perk implementation and register in implementation table.

        Args:
            impl: Perk implementation AST node
        """
        perk_name = getattr(impl, "perk_name", None)
        if not isinstance(perk_name, str):
            return

        perk_name_span: Optional[Span] = getattr(impl, "perk_name_span", None) or getattr(impl, "loc", None)
        target_type: Optional[Type] = getattr(impl, "target_type", None)

        # Extract type name from target type
        type_name = _get_type_name(target_type)
        if type_name is None:
            # Can't determine type name, skip
            return

        # Check if perk exists
        if not self.perks.get(perk_name):
            er.emit(self.r, ERR.CE4003, perk_name_span, perk=perk_name)
            return

        # Register the implementation
        if not self.perk_impls.register(impl, type_name):
            # Duplicate implementation
            er.emit(self.r, ERR.CE4002, getattr(impl, "loc", None), type=type_name, perk=perk_name)
            return
