"""The typecheck pass: type validation and inference."""
from __future__ import annotations
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.namespaces import Binding, NamespaceTable
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.passes.collect.externals import ExternalSig
    from sushi_lang.semantics.passes.collect.functions import FuncSig
    from sushi_lang.semantics.passes.collect.constants import ConstSig

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ExtendDef, Block, Stmt, Expr
)
from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.types.visitor import StatementValidator, ExpressionValidator, TypeInferenceVisitor

from .compatibility import types_compatible
from .constants import validate_constant
from .public_signatures import check_public_signatures
from .signatures import (
    validate_function,
    validate_extension_method,
    validate_perk_implementation_method,
)


class TypeValidator:
    """The typecheck pass: type validation and inference."""

    def __init__(self, reporter: Reporter, tables: 'SymbolTables',
                 current_unit_name: Optional[str] = None,
                 monomorphized_functions: Optional[Dict[str, tuple]] = None,
                 in_library_unit: bool = False,
                 namespaces: Optional['NamespaceTable'] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.tables = tables
        self.const_table = tables.constants
        self.struct_table = tables.structs
        self.enum_table = tables.enums
        self.func_table = tables.funcs
        self.external_table = tables.externals
        self.extension_table = tables.extensions
        self.generic_enum_table = tables.generic_enums
        self.generic_struct_table = tables.generic_structs
        self.generic_extension_table = tables.generic_extensions
        self.generic_func_table = tables.generic_funcs
        self.perk_table = tables.perks
        self.perk_impl_table = tables.perk_impls
        # This program's auto-derived hash() and clone(), and the process-wide built-ins
        # behind them: one lookup answers both (#601).
        self.derived_methods = tables.derived_methods
        # The types that implement `Drop`, for `owns_resource` (ruling R2a). A property
        # rather than a snapshot: a later unit may add an implementation, and a stale
        # set here would classify a handle PLAIN.
        self.library_not_exported = tables.library_not_exported
        self.visibility = tables.visibility
        # What this unit may write behind a dot. One seam for an FFI namespace and a
        # `use ... as` alias alike; with no unit of its own a validator still reaches
        # every FFI namespace, which is what `externals_only` gives it.
        from sushi_lang.semantics.namespaces import externals_only
        self.namespaces = (namespaces if namespaces is not None
                           else externals_only(self.external_table))
        self.current_unit_name = current_unit_name  # Track which unit is being validated (for visibility checking)
        self.monomorphized_functions = monomorphized_functions or {}
        # Every builtin a program may WRITE: the numeric set, plus the three that are
        # not numeric.
        self.known_types: Set[BuiltinType] = BUILTIN_NUMERIC_TYPES | {
            BuiltinType.BOOL, BuiltinType.STRING, BuiltinType.BLANK,
        }
        self.current_function: Optional[FuncDef] = None
        # Whose code is being validated. A source library's unit is compiled at the
        # consumer, and its bodies mention whatever the consumer's call substituted into
        # a template -- a private type of the consumer's included. The consumer must not
        # be shown a diagnostic about code they did not write, which is the same reason
        # the `docs` pass skips a library unit whole.
        self.in_library_unit = in_library_unit
        # A library body transplanted into this program: a monomorphized instance of a
        # `.slib` template, or a lambda lifted out of one. It calls what it called at
        # home, the export closure included (#468).
        self.in_library_body = in_library_unit
        # A body the compiler wrote: a monomorphized instance, whose type arguments were
        # chosen at the instantiation site and checked in the scope that wrote them. The
        # template's own unit never imported them and never could (section 6).
        self.in_synthesized_body = False
        self.variable_types: Dict[str, Type] = {}
        self.destroyed_arrays: List[set[str]] = []

        self.statement_validator = StatementValidator(self)
        self.expression_validator = ExpressionValidator(self)
        self.type_inference_visitor = TypeInferenceVisitor(self)

    def run(self, program: Program) -> None:
        """Entry point for type validation."""
        # Whole-unit, and BEFORE the per-declaration walk: it is the only way a public
        # generic is reached, since the loop below skips one.
        check_public_signatures(self, program)

        # The same reason, for the same reader: a constraint rides on a declaration's
        # type parameters, and the loop below never visits a generic function.
        from .qualified import check_qualified_constraints
        check_qualified_constraints(self, program)

        # The same reason again: a struct and an enum have no body, so the loop below
        # never visits one and their written types reached no check at all (#504).
        from .signatures import validate_declared_types
        validate_declared_types(self, program)

        # And the position beside a declared type: the perk a constraint names (#505).
        from .perks import check_constraint_perks
        check_constraint_perks(self, program)

        for const in program.constants:
            validate_constant(self, const)

        for func in program.functions:
            if hasattr(func, 'type_params') and func.type_params:
                continue
            self._validate_function(func)

        for ext in program.extensions:
            self._validate_extension_method(ext)

        for impl in program.perk_impls:
            validate_perk_implementation_method(self, impl)

    @property
    def drop_type_names(self) -> frozenset:
        """The types that implement `Drop`, the second way a type can own something."""
        return frozenset(self.perk_impl_table.by_perk.get("Drop", ()))

    def func_sig(self, name: str) -> Optional['FuncSig']:
        """What the name of a function means INSIDE the unit being validated.

        The unit's own declaration answers its own call, and every other name resolves
        through the flat view. Reading `by_name` directly measured a unit's call against
        another unit's declaration of the same name, which is section 13.1 of
        `docs/design/unit-namespaces.md` -- #487 for a library, and an ICE (CE0026) for
        two ordinary units once the two symbols were allowed to coexist.
        """
        return self.func_table.lookup(name, self.current_unit_name, self.scope)

    def const_sig(self, name: str) -> Optional['ConstSig']:
        """What the name of a constant means INSIDE the unit being validated."""
        return self.const_table.lookup(name, self.current_unit_name, self.scope)

    def generic_sig(self, name: str):
        """What the name of a GENERIC function means INSIDE the unit being validated.

        The same ladder `func_sig` walks, over the generic table's two views (#495).
        """
        return self.generic_func_table.lookup(name, self.current_unit_name, self.scope)

    @property
    def scope(self):
        """What this unit may write with no qualifier (section 6)."""
        return self.namespaces.scope

    def namespaces_of(self, unit_name: Optional[str]):
        """Any unit's namespace table, for a reader that folds another unit's constant (#561)."""
        if unit_name == self.current_unit_name:
            return self.namespaces
        return self.tables.namespaces.get(unit_name)

    def constant_evaluator(self, reporter: Optional[Reporter] = None):
        """The evaluator for this unit's constant expressions. Silent with no reporter."""
        from sushi_lang.semantics.const_eval import ConstantEvaluator
        return ConstantEvaluator(reporter if reporter is not None else Reporter(),
                                 self.const_table, self.current_unit_name,
                                 self.namespaces_of, self.struct_table, self.enum_table)

    def validate_expression(self, expr: Expr) -> Optional[Type]:
        """Validate an expression and its subexpressions using the Visitor Pattern."""
        self.expression_validator.visit(expr)

        return self.infer_expression_type(expr)

    def infer_expression_type(self, expr: Expr) -> Optional[Type]:
        """Infer the type of an expression using the Visitor Pattern."""
        return self.type_inference_visitor.visit(expr)

    def namespace_of(self, receiver) -> Optional[str]:
        """The namespace a receiver names, or None when it names something else.

        Local-wins, and it is the whole of section 8's first row: a variable named
        `libc` or `geo` shadows the namespace for the rest of its scope.
        """
        from sushi_lang.semantics.ast import Name
        if not isinstance(receiver, Name):
            return None
        if receiver.id in self.variable_types:
            return None
        return receiver.id if self.namespaces.is_namespace(receiver.id) else None

    def resolve_namespaced(self, receiver, name: str) -> Optional['Binding']:
        """What `<namespace>.<name>` denotes. The ONE reader, for every kind."""
        ns = self.namespace_of(receiver)
        return None if ns is None else self.namespaces.lookup(ns, name)

    def _resolve_external_call(self, node) -> Optional['ExternalSig']:
        """Resolve a DotCall to a foreign function signature, if applicable."""
        binding = self.resolve_namespaced(node.receiver, node.method)
        if binding is None or binding.kind != "extern":
            return None
        node.external_ref = (binding.provider.origin, node.method)
        return binding.record

    def _validate_function(self, func: FuncDef) -> None:
        """The pass's surface for the `lift` pass (#756).

        The other twenty-one delegates of this shape went: a caller inside the pass
        names the module function it wants. A caller OUTSIDE the pass holds only the
        validator, so the three that serve one keep their place here.
        """
        validate_function(self, func)

    def annotate_function(self, func: FuncDef) -> None:
        """Type one function the lambda lifter built (the `Annotator` contract, #687).

        A lifted lambda is an ordinary function from here on, so it is typed by the path
        an ordinary one takes. Public because the lift pass runs after this one and
        depends on it: a lifted body carries no parameter types and no channel until
        this runs over it. The lift pass used to name `_validate_function` instead, so a
        rename of a PRIVATE method here broke a different pass with no diagnostic.
        """
        self._validate_function(func)

    def _validate_extension_method(self, ext: ExtendDef) -> None:
        """The pass's surface for the analyzer, which drives the per-unit loop."""
        validate_extension_method(self, ext)

    def _validate_block(self, block: Block) -> None:
        """Validate statements in a block."""
        for stmt in block.statements:
            self._validate_statement(stmt)

    def _validate_statement(self, stmt: Stmt) -> None:
        """Validate a statement using the Visitor Pattern."""
        self.statement_validator.visit(stmt)

    def _types_compatible(self, actual: Type, expected: Type) -> bool:
        """The pass's surface for the Maybe and Result intern seams."""
        return types_compatible(self, actual, expected)


__all__ = ['TypeValidator']
