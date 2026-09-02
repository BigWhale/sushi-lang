"""The instantiate pass: collect every generic instantiation the program asks for."""
from __future__ import annotations
from typing import Set, Tuple, TYPE_CHECKING
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

    visited_types: Set[str] = field(default_factory=set)

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

        # Build the typecheck pass's real inferrer over the same tables, with a discard reporter so
        # any diagnostics it raises never reach the user (they belong to the typecheck pass, which
        # runs later and emits them for real). It shares this collector's variable_types
        # dict, so the scope the collector builds as it walks is the scope the inferrer
        # sees. Constructed only when the whole SymbolTables is available.
        type_validator = self._build_shared_inferrer()

        expression_scanner = ExpressionScanner(
            type_inferrer=type_inferrer,
            instantiations=self.instantiations,
            function_instantiations=self.function_instantiations,
            generic_funcs=self.generic_funcs or {},
            type_validator=type_validator,
            namespaces=self.namespaces,
            generic_enums=self._generic_enums_by_name(),
        )

        function_collector = FunctionCollector(
            expression_scanner=expression_scanner,
            instantiations=self.instantiations,
            variable_types=self.variable_types,
            visited_types=self.visited_types,
        )

        expression_scanner.scan_block = function_collector._collect_from_block
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

        # A perk method returns a bare type like an extension, but its parameters and
        # body still carry generic instantiations.
        for perk_impl in program.perk_impls:
            function_collector.collect_from_perk_impl(perk_impl)

        return self.instantiations, self.function_instantiations

    # An extension on a GENERIC target is monomorphized per instantiation of that target,
    # so its signature can only be read once the target instantiations are known -- which
    # is after every unit has been walked. Hence the second entry point rather than another
    # loop inside `run`.
    #
    # A substituted signature may itself instantiate a generic that carries its own
    # generic-target extension, so this iterates. The bound only exists so a pathological
    # program cannot spin: reaching it drops an instantiation, which surfaces as the
    # ordinary CE2001, never as a hang.
    MAX_EXPANSION_ROUNDS = 8

    def collect_from_generic_extensions(self, programs) -> None:
        """Signature instantiations of every generic-TARGET extension (#389).

        `program.extensions` holds only the plain-target ones -- the AST builder files an
        extension whose target is spelled `@(...)` under `generic_extensions`, CONCRETE
        arguments included. Nothing walked that list, so a generic type named in such a
        signature was never collected and the declaration was a false CE2001, for a type
        the program plainly declares.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef, substitute_type_params
        from sushi_lang.semantics.generics.extension_targets import target_shape_of

        function_collector = self._build_function_collector()

        for _round in range(self.MAX_EXPANSION_ROUNDS):
            before = len(self.instantiations)

            for program in programs:
                for ext in getattr(program, "generic_extensions", None) or ():
                    target = ext.target_type
                    if not isinstance(target, GenericTypeRef):
                        continue

                    shape = target_shape_of(ext)
                    if shape is not None and shape.is_concrete:
                        # A concrete target names its types outright, so its signature needs
                        # no instantiation to be read -- and reading it per instantiation of
                        # the base name would substitute `Box@(Point)`'s `Point` away (#393).
                        for declared in (ext.ret, *(p.ty for p in ext.params)):
                            if declared is not None:
                                function_collector._collect_from_type(declared)
                        function_collector._reset_scope()
                        function_collector._collect_from_block(ext.body)
                        continue

                    param_names = [str(arg) for arg in target.type_args]
                    for base_name, type_args in list(self.instantiations):
                        if base_name != target.base_name or len(type_args) != len(param_names):
                            continue

                        substitution = dict(zip(param_names, type_args, strict=True))
                        for declared in (ext.ret, *(p.ty for p in ext.params)):
                            if declared is None:
                                continue
                            function_collector._collect_from_type(
                                substitute_type_params(declared, substitution))

                    # The BODY's own annotations are collected as written. For a concrete
                    # target they already are concrete; for a template they still name the
                    # type parameter, and `_collect_from_type` drops what it cannot resolve.
                    function_collector._reset_scope()
                    function_collector._collect_from_block(ext.body)

            if len(self.instantiations) == before:
                return

    def _build_shared_inferrer(self):
        """The typecheck pass's TypeValidator over the same tables, wired to discard diagnostics."""
        if self.tables is None:
            return None
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.passes.types import TypeValidator

        validator = TypeValidator(Reporter(), self.tables)
        validator.variable_types = self.variable_types
        return validator
