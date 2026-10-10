"""The instantiate pass: collect every generic instantiation the program asks for."""
from __future__ import annotations
from typing import Optional, Set, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.ast import Program

from sushi_lang.semantics.generics.instantiate.types import TypeInferrer
from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner
from sushi_lang.semantics.generics.instantiate.functions import FunctionCollector


@dataclass
class InstantiationCollector:
    """Collects all generic type instantiations used in a program."""

    # Set of (base_name, type_args) tuples representing unique instantiations
    # Examples:
    #   - ("Result", (BuiltinType.I32,)) for Result<i32>
    #   - ("Pair", (BuiltinType.I32, BuiltinType.STRING)) for Pair<i32, string>
    # The base_name distinguishes between generic enums and generic structs.
    instantiations: Set[Tuple[str, Tuple["Type", ...]]] = field(default_factory=set)

    # Set of (declaring_unit, function_name, type_args) triples for generic function
    # instantiations (#495, D2). The unit comes from the resolved GenericFuncDef, so two
    # units' generics of one name stay two identities.
    function_instantiations: Set[Tuple[str | None, str, Tuple["Type", ...]]] = field(default_factory=set)

    struct_table: dict | None = field(default=None)

    enum_table: dict | None = field(default=None)

    # Generic struct table for checking if a base_name refers to a generic struct
    # This is used to distinguish generic struct instantiations from generic enum instantiations
    generic_structs: dict | None = field(default=None)

    generic_funcs: dict | None = field(default=None)

    # Plain (non-generic) top-level function table (name -> FuncSig), used to present a
    # FunctionType for a bare function reference passed as a higher-order argument.
    func_table: dict | None = field(default=None)

    # The whole-program SymbolTables. When present, the instantiate pass infers generic-call
    # argument and receiver types through the typecheck pass's own TypeValidator instead of a thin
    # parallel inferrer -- the two used to disagree, and every method the typecheck pass knew and
    # the instantiate pass did not dropped an instantiation on the floor (CE2061; issues #171/#191).
    tables: object | None = field(default=None)

    # What the unit being walked may write behind a dot. `alias.generic(...)` is a
    # generic call, and a collector that cannot tell one from an ordinary method call
    # drops the instantiation (CE2061).
    namespaces: object | None = field(default=None)

    variable_types: dict[str, "Type"] = field(default_factory=dict)

    # The instantiations whose RESOLVED arguments are walked already, so a type that
    # holds an instance of itself ends the walk (`instantiate/type_collection.py`).
    visited_types: Set[str] = field(default_factory=set)

    # The FIRST site that names each instantiation, keyed by its interned name (a type)
    # or by ("fn", interned name) for a generic function: `(span, filename)`. A
    # constraint violation is reported there (#579), because the instantiation set
    # itself carries no location. The analyzer sets `current_file` per unit.
    sites: dict = field(default_factory=dict)
    current_file: Optional[str] = None
    # The instantiation whose template writes a site, by key: a type that a generic
    # call's substituted signature names has its site in the template, and the call
    # that names the function is its parent (`Monomorphizer.parents`).
    parents: dict = field(default_factory=dict)

    def _generic_enums_by_name(self) -> dict:
        """The generic enum templates, keyed by name. Empty when the tables are absent."""
        tables = self.tables
        if tables is None:
            return {}
        generic_enums = getattr(tables, "generic_enums", None)
        return getattr(generic_enums, "by_name", None) or {}

    def _build_function_collector(self) -> FunctionCollector:
        """The collaborator trio, wired. Both entry points walk types the same way."""
        type_inferrer = TypeInferrer(
            variable_types=self.variable_types,
            struct_table=self.struct_table or {},
            enum_table=self.enum_table or {},
            func_table=self.func_table or {},
        )

        # The typecheck pass's inference, read-only (#806). It shares this collector's
        # variable_types dict, so the scope the collector builds as it walks is the scope
        # the inferrer sees. Constructed only when the whole SymbolTables is available.
        type_validator = self._build_shared_inferrer()

        expression_scanner = ExpressionScanner(
            type_inferrer=type_inferrer,
            instantiations=self.instantiations,
            function_instantiations=self.function_instantiations,
            generic_funcs=self.generic_funcs or {},
            type_validator=type_validator,
            namespaces=self.namespaces,
            generic_enums=self._generic_enums_by_name(),
            sites=self.sites,
            file_of=lambda: self.current_file,
            parents=self.parents,
        )

        function_collector = FunctionCollector(
            expression_scanner=expression_scanner,
            instantiations=self.instantiations,
            variable_types=self.variable_types,
            visited_types=self.visited_types,
            sites=self.sites,
            file_of=lambda: self.current_file,
            parents=self.parents,
        )

        expression_scanner.scan_block = function_collector.collect_from_body
        expression_scanner.collect_type = function_collector._collect_from_type
        return function_collector

    def run(self, program: "Program") -> Tuple[Set[Tuple[str, Tuple["Type", ...]]], Set[Tuple[str, Tuple["Type", ...]]]]:
        """Entry point for instantiation collection."""
        function_collector = self._build_function_collector()

        for const in program.constants:
            function_collector.collect_from_const(const)

        # Collect from struct definitions
        # This ensures that generic types used as struct fields (e.g., Maybe<i32>)
        # are properly monomorphized before codegen
        for struct in program.structs:
            function_collector.collect_from_struct(struct)

        # Collect from enum definitions
        # This ensures that generic types used in enum variants (e.g., Own<Expr>)
        # are properly monomorphized before codegen
        for enum in program.enums:
            function_collector.collect_from_enum(enum)

        for func in program.functions:
            function_collector.collect_from_function(func)

        for ext in program.extensions:
            function_collector.collect_from_extension(ext)

        # A perk CONTRACT has no body, so no body walk reaches it. Its written types
        # still name instantiations, and its channel is the one the implementation must
        # agree with (CE0133).
        for perk in program.perks:
            function_collector.collect_from_perk(perk)

        # A perk method returns a bare type like an extension, but its parameters and
        # body still carry generic instantiations.
        for perk_impl in program.perk_impls:
            function_collector.collect_from_perk_impl(perk_impl)

        return self.instantiations, self.function_instantiations

    def collect_from_signatures(self, signatures) -> None:
        """The instantiations a library's manifest signatures name (#543).

        A `FuncSig` read from a binary `.slib` belongs to no unit, so `run` never sees
        it; its return and parameters still name instantiations the consumer has to cut.
        """
        function_collector = self._build_function_collector()
        for sig in signatures:
            function_collector._reset_scope()
            function_collector.collect_from_signature(
                sig.ret_type, getattr(sig, "err_type", None), sig.params)

    def _build_shared_inferrer(self):
        """The typecheck pass's inference over the same tables: no diagnostic, no stamp."""
        if self.tables is None:
            return None
        from sushi_lang.semantics.passes.types import ReadOnlyInferrer

        validator = ReadOnlyInferrer(self.tables, self.namespaces)
        validator.variable_types = self.variable_types
        return validator
