"""The typecheck pass: type validation and inference (docs/design/ir.md 7.3, 7.9).

The pass has two halves (ruling E1). The DECLARATION half reads the AST: the public
signatures, the qualified constraints, the declared types, the constraint perks, the
declared type of a constant and the header of each function, method and perk
implementation. The BODY half walks the SHIR body of each declaration (`check_body`)
through the table of `visit/`, and it writes its answers into the `TypeckResults` of the
body. A stop of the body half ends the walk of its body, and the pass records it in
`stops` (`shir/stop.py`); the next body is walked as usual. When a walk ends with no stop
and with no error in its body, the completeness check of the table runs (`check_answers`),
and the pass gives the body to the next stage, the lift pass (`next_stage`), which the
driver gives the pass.
"""
from __future__ import annotations
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.namespaces import Binding, NamespaceTable
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.conversions import Conversion
    from sushi_lang.semantics.passes.collect.functions import FuncSig
    from sushi_lang.semantics.passes.collect.constants import ConstSig
    from sushi_lang.semantics.passes.types.constants import InitializerFacts
    from sushi_lang.semantics.passes.types.templates import PackElements
    from sushi_lang.semantics.shir.body import ShirBody, ShirLocal
    from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
    from sushi_lang.semantics.shir.lowering import LoweredProgram
    from sushi_lang.semantics.const_eval import ConstantValue

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Program, FuncDef, ExtendDef, Expr
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.types.visit.inference import TypeInferenceVisitor
from sushi_lang.semantics.passes.types.visit.statements import check_block, check_params
from sushi_lang.semantics.passes.types.fold import Folder
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.shir.stop import Stops
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.shir.typeck_check import check_answers

from .constants import validate_constant
from .public_signatures import check_public_signatures
from .signatures import (
    validate_function,
    validate_extension_method,
    validate_perk_implementation_method,
)


class TypeValidator:
    """The typecheck pass: type validation and inference."""

    # The typecheck pass enters a late extension copy: its signature in the extension
    # table and its body in the copy queue. An inference before it does not (#1153).
    queues_late_copies = True

    # True only over the check of a template (`templates.TemplateValidator`, #1070).
    in_template_check = False
    # The element type of each `expand`, over the check of a pack template only.
    pack_elements: Optional['PackElements'] = None

    def __init__(self, reporter: Reporter, tables: 'SymbolTables',
                 current_unit_name: Optional[str] = None,
                 monomorphized_functions: Optional[Dict[str, tuple]] = None,
                 in_library_unit: bool = False,
                 namespaces: Optional['NamespaceTable'] = None,
                 checks_templates: bool = False,
                 shir: Optional['LoweredProgram'] = None,
                 stops: Optional[Stops] = None,
                 next_stage: Optional[Callable[..., None]] = None) -> None:
        self.reporter = reporter
        # Whether `run` checks each function template of the unit (#1070): a unit of the
        # program, and a bundled stdlib unit. A consumed library
        # unit was checked by its own `--lib` build.
        self.checks_templates = checks_templates
        # The written templates that `run` checked.
        self.checked_template_ids: Set[int] = set()
        # The SHIR bodies of the program, the record of the stops of the body half, and the
        # stage that each body goes to when the body half finished it with no error: the
        # lift pass (`LiftPass.check_body`).
        self.shir = shir
        self.stops = stops
        self.next_stage = next_stage
        # The SHIR body that the body half walks, its `TypeckResults`, its locals by id and
        # the fold of its readable values (docs/design/ir.md 7.4), or None.
        self.body: Optional['ShirBody'] = None
        self.answers: Optional[TypeckResults] = None
        self.body_locals: Dict['LocalId', 'ShirLocal'] = {}
        # The locals of the body whose `let` the pass refused (CE2002, an unhandled
        # `Result`): a method call on one reports no CE2008 (#1128). `_check_let` writes it.
        self.refused_locals: set['LocalId'] = set()
        # The value pack parameters of the body of a template check (ir.md 4.5).
        self.value_packs: set['LocalId'] = set()
        # The matches of the body whose arms miss a value (CE2040, CE2074): the
        # dead-statement rule does not take one as the end of a path (#886).
        self.uncovered_matches: set['ShirId'] = set()
        self.folder: Optional[Folder] = None
        # True when the body holds an error that an earlier pass reported: the collect
        # pass refuses a `??` in a bare body (CE0131), and its node has no answer.
        self.refused_before = False
        # The parameters by address of the lambdas around the node that the walk is in.
        self.lambda_borrows: Set['LocalId'] = set()
        # The initializer that the body half walks: its declaration and the value that the
        # constant evaluator gave it (docs/design/ir.md 7.3), or None.
        self.initializer: Optional['InitializerFacts'] = None
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
        # The body being validated, whatever its kind (signatures.py `_enter_body`): how
        # a diagnostic names it, the return type it declares, and the Result it answers
        # (None for a bare body).
        self.body_name: Optional[str] = None
        self.body_return_type: Optional[Type] = None
        # The conversion whose body this is (`ExtendDef.declared_conversion`), or None.
        self.body_conversion: Optional["Conversion"] = None
        # The key of the copy whose body this is (`FuncDef.instance_key`), or None: a
        # call-site copy that the body names records it as its parent.
        self.body_instance_key: Optional[tuple] = None
        self.channel_result: Optional[Type] = None
        # True while the body is a copy of a template (`ast_walk.is_template_copy`): the
        # statement rules that need no type ran once on the written template (#1070).
        self.in_template_copy: bool = False
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
        # The `let` names whose initializer was refused; a method call on one is not
        # refused again (#1128). Only the AST port template `validate_method_call` (C3)
        # reads and writes it: the SHIR walk keeps `refused_locals`.
        self.refused_bindings: set[str] = set()
        # The ranges refused outside their two positions (#1165): each answers no type
        # after the refusal, so the position that holds it adds no second error.
        self.refused_ranges: set[int] = set()

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

        # A template is checked one time, where it is written (#1070):
        # a function template, an extension template and a perk template. The copy reads
        # the statement rules too, with the stamps it gets.
        from .templates import (
            check_extension_template, check_function_template, check_perk_template)
        checked = self.checked_template_ids
        for func in program.functions:
            if func.type_params:
                if self.checks_templates and check_function_template(self, func):
                    checked.add(id(func))
                continue
            self._validate_function(func)
        for template in program.generic_extensions:
            if self.checks_templates and check_extension_template(self, template):
                checked.add(id(template))

        from .perks import validate_template_header
        for impl in program.generic_perk_impls:
            self.reporter.leave_body()
            validate_template_header(self, impl)
            if self.checks_templates and check_perk_template(self, impl):
                checked.add(id(impl))

        for ext in program.extensions:
            self._validate_extension_method(ext)

        for impl in program.perk_impls:
            # The header is read as written, never as a copy of a body (#800).
            self.reporter.leave_body()
            validate_perk_implementation_method(self, impl)
        self.reporter.leave_body()

    @property
    def drop_type_names(self) -> frozenset:
        """The types that implement `Drop`, the second way a type can own something."""
        return drop_type_names(self.perk_impl_table)

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
                                 self.namespaces_of, self.struct_table, self.enum_table,
                                 functions=self.func_table)

    def check_body(self, decl,
                   path_faults: Optional[Callable[['ShirBody', Set['ShirId']], None]] = None,
                   *, offered: Optional[int] = None) -> Optional['ShirBody']:
        """The body half over the SHIR body of `decl` (docs/design/ir.md 7.3, 7.9).

        A stop ends the walk: the pass records it, with the file of the body, and the
        caller goes on with the next declaration. When the walk reaches its end, the path
        faults of a function or a method (`path_faults`) are checked, with the matches of
        the body whose arms miss a value. A walk that ends with no stop and with no error
        in its body is checked for completeness, and its body goes to the next stage, the
        lift pass, with the tables that the body was read in. A node that has an error has
        no answer, so a body with an error is not checked: it goes to the lift pass marked
        `typecheck_failed` when its header has no error (docs/design/ir.md 7.9). `offered` is the count of the errors before the header of
        a function or a method, so that a body whose header has an error (an unknown
        parameter type) does not go on.
        The answer is the body when the walk reached its end, and None after a stop.
        """
        body = None if self.shir is None else self.shir.body_of(self.written_of(decl))
        if (body is None or self.shir is None or self.stops is None
                or self.next_stage is None):
            er.raise_internal_error(
                "CE0000", detail=f"the typecheck pass has no SHIR body of "
                                 f"'{getattr(decl, 'name', type(decl).__name__)}'")
        self.body = body
        self.answers = answers = self.shir.typeck_of(body)
        self.body_locals = {local.id: local for local in body.locals}
        self.value_packs = {param.local for param in body.params if param.pack}
        self.refused_locals = set()
        self.uncovered_matches = set()
        self.folder = Folder(self._constant_value, self.written_type)
        self.refused_before = False
        origin = self.reporter.origin
        filename = origin.filename if origin is not None else self.reporter.filename
        if offered is None:
            offered = self.errors_offered()
        try:
            check_params(self, body)
            header_failed = self.errors_offered() != offered
            check_block(self, body.block)
            walk_failed = self.errors_offered() != offered or self.refused_before
        except StageStop as stop:
            stop.filename = filename
            self.stops.stopped(stop)
            return None
        finally:
            uncovered = self.uncovered_matches
            self.body = self.answers = self.folder = None
            self.body_locals = {}
            self.value_packs = set()
            self.refused_locals = set()
            self.uncovered_matches = set()
        if path_faults is not None:
            path_faults(body, uncovered)
        if header_failed:
            return body
        if walk_failed or self.errors_offered() != offered:
            self.next_stage(decl, body, tables=self.tables, filename=filename,
                            typecheck_failed=True)
            return body
        check_answers(body, answers, self.shir.decls)
        self.next_stage(decl, body, tables=self.tables, filename=filename)
        return body

    def errors_offered(self) -> int:
        """The count of the errors offered so far: a check that adds one refused its node."""
        return sum(self.reporter.errors_offered.values())

    def body_answers(self) -> TypeckResults:
        """The `TypeckResults` of the body that the body half walks."""
        if self.answers is None:
            er.raise_internal_error("CE0000", detail="the typecheck pass walks no SHIR body")
        return self.answers

    def body_folder(self) -> Folder:
        """The readable fold of the body that the body half walks (docs/design/ir.md 7.4)."""
        if self.folder is None:
            er.raise_internal_error("CE0000", detail="the typecheck pass walks no SHIR body")
        return self.folder

    def decl_name(self, decl: 'DeclId') -> str:
        """The written name of a declaration that a SHIR node names."""
        if self.shir is None:
            er.raise_internal_error("CE0000", detail="the typecheck pass has no SHIR program")
        return self.shir.decls.name_of(decl)

    def method_decl(self, owner: Type, method: str) -> 'DeclId':
        """The id of the written name `<type>.<method>` of a method that a call names: the
        name that lowering gives the body of the method (docs/design/ir.md 7.6)."""
        if self.shir is None:
            er.raise_internal_error("CE0000", detail="the typecheck pass has no SHIR program")
        from sushi_lang.semantics.generics.type_display import display_type
        return self.shir.decls.refer(f"{display_type(owner)}.{method}")

    def _constant_value(self, decl: 'DeclId') -> Optional['ConstantValue']:
        """The value of a constant that a `ConstRef` names, from the constant table (Q5)."""
        from sushi_lang.semantics.passes.types.visit.expressions import constant_record
        record = constant_record(self, self.decl_name(decl))
        return None if record is None else self.constant_value(record)

    def constant_value(self, record) -> Optional['ConstantValue']:
        """The value of a declared constant or of a stdlib constant, or None when its
        initializer has an error."""
        return self.constant_evaluator().value_of(record, None)

    def written_of(self, decl):
        """The declaration that lowering made the SHIR body of: `decl` itself here. The
        check of a template walks the body of the template as written."""
        return decl

    def written_type(self, ty: Type) -> Type:
        """A type that a SHIR body writes, as the body half reads it: as written here. The
        check of a template reads it at the opaque type parameters."""
        return ty

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

    def _validate_function(self, func: FuncDef) -> None:
        """The pass's surface for the `lift` pass (#756).

        The other twenty-one delegates of this shape went: a caller inside the pass
        names the module function it wants. A caller OUTSIDE the pass holds only the
        validator, so the three that serve one keep their place here.
        """
        validate_function(self, func)

    def _validate_extension_method(self, ext: ExtendDef) -> None:
        """The pass's surface for the analyzer, which drives the per-unit loop."""
        validate_extension_method(self, ext)


class ReadOnlyInferrer(TypeValidator):
    """The typecheck pass's inference, for a pass that runs BEFORE the typecheck pass (#806).

    The `instantiate` and `monomorphize` passes type a generic call's arguments with it.
    Inference writes stamps on the nodes it types, and the backend reads those stamps,
    so an early pass that kept them would leave values read from tables that are not
    complete yet. This inferrer puts every field of every node under the expression back
    when the outermost inference returns, and its diagnostics go to a Reporter that
    nobody reads: the typecheck pass reports them later, for real.

    Only the nodes under the expression are put back. A node that inference builds for
    itself (the `MethodCall` view of a `DotCall`) is dropped when inference returns.

    `namespaces` is the table of the unit that holds the expression, as the typecheck
    pass gives it: `sh.Pt(0)` names what `Pt(0)` names under a flat import only when
    the inferrer knows the alias (#1147). Without it the inferrer has no unit.

    It queues no late copy of a generic static either: the copy of an instance the
    monomorphize pass reaches is cut by that pass, and a second copy from the queue
    was a duplicate symbol (#1153). The typecheck pass queues what is still missing.
    """

    queues_late_copies = False

    def __init__(self, tables: 'SymbolTables',
                 namespaces: Optional['NamespaceTable'] = None) -> None:
        unit = namespaces.scope.unit if namespaces is not None else None
        super().__init__(Reporter(), tables, current_unit_name=unit,
                         namespaces=namespaces)
        self.type_inference_visitor = TypeInferenceVisitor(self)
        self._depth = 0

    def infer_expression_type(self, expr: Expr) -> Optional[Type]:
        """The type of an AST expression, through the inference visitor of the early passes."""
        if self._depth:
            return self.type_inference_visitor.visit(expr)
        self._depth += 1
        try:
            with fields_restored_under(expr):
                return self.type_inference_visitor.visit(expr)
        finally:
            self._depth -= 1


@contextmanager
def fields_restored_under(root: Expr) -> Iterator[None]:
    """Put back every declared field of every node under `root` when the block ends."""
    from sushi_lang.semantics.ast_walk import node_fields, walk_nodes

    saved: list = []

    def visit(node) -> bool:
        saved.append((node, list(node_fields(node))))
        return True

    walk_nodes(root, visit)
    try:
        yield
    finally:
        for node, values in saved:
            for name, value in values:
                setattr(node, name, value)


__all__ = ['ReadOnlyInferrer', 'TypeValidator', 'fields_restored_under']
