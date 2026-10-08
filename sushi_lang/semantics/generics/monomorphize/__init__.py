"""The monomorphize pass: every generic definition becomes concrete instances."""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager, nullcontext
from typing import Dict, Iterator, Optional, Tuple, Set, TYPE_CHECKING
from dataclasses import dataclass, field

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.typesys import Type, EnumType, StructType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.internals.report import Reporter, Span

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import FuncDef
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef, FunctionTable
    from sushi_lang.semantics.passes.collect.enums import EnumTable
    from sushi_lang.semantics.passes.collect.structs import StructTable
    from sushi_lang.semantics.generics.constraints import ConstraintValidator
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error

from .transformer import TypeSubstitutor
from .types import TypeMonomorphizer, MonomorphizationDepthExceeded
from .functions import FunctionMonomorphizer

__all__ = ["Monomorphizer", "MonomorphizationDepthExceeded"]


@dataclass
class Monomorphizer:
    """Generates concrete enum types, struct types, and function definitions from generic
    definitions.
    """

    reporter: Reporter
    constraint_validator: 'ConstraintValidator | None' = None
    cache: Dict[Tuple[str, Tuple[Type, ...]], EnumType] = field(default_factory=dict)
    struct_cache: Dict[Tuple[str, Tuple[Type, ...]], StructType] = field(default_factory=dict)
    # Keyed (declaring_unit, name, type_args) -- D2 of #495.
    func_cache: Dict[Tuple[str | None, str, Tuple[Type, ...]], 'FuncDef'] = field(default_factory=dict)
    generic_enums: Dict[str, GenericEnumType] = field(default_factory=dict)
    generic_structs: Dict[str, GenericStructType] = field(default_factory=dict)
    # The collect pass's GenericFunctionTable on the compiler path (both views);
    # a plain name-keyed dict on unit-test paths.
    generic_funcs: object = field(default_factory=dict)
    func_table: 'FunctionTable | None' = None
    monomorphized_functions: Dict[str, Tuple[str | None, str, Tuple[Type, ...]]] = field(default_factory=dict)
    enum_table: 'EnumTable | None' = None
    struct_table: 'StructTable | None' = None
    # Whole-program SymbolTables. Lets the nested-call collector infer a generic call's
    # argument types through the typecheck pass's TypeValidator instead of the old Names-only inferrer,
    # so a non-Name argument (a call, cast, or method result) no longer aborts inference and
    # drops the instantiation (issue #214). None on unit-test paths built from loose tables.
    tables: object | None = None
    pending_instantiations: Set[Tuple[str | None, str, Tuple[Type, ...]]] = field(default_factory=set)
    # None while the monomorphize stage runs: a function copy goes into its home unit's
    # AST at once, and the per-unit passes check it. The analyzer sets a list before the
    # per-unit loop: a copy cut after that (a late request of the typecheck pass, or a
    # call in an extension copy) waits here as (AST, body), and the analyzer puts it into
    # its AST and checks it after the loop (#1155).
    late_bodies: Optional[list] = None
    # The instantiate pass's site table (#579): the first span that named each
    # instantiation, keyed by interned name, so a constraint violation has a caret.
    sites: dict = field(default_factory=dict)
    # The file of each unit, by name: the site of an instantiation that a COPY's body
    # names is in the file of the copy's template, so a constraint refusal of it has a
    # location (#1070).
    unit_files: Dict[str, str] = field(default_factory=dict)
    # How many instantiations a constraint refused. The analyzer STOPS the whole-program
    # analysis after the monomorphize step when this is non-zero (Ruling 4, #579): no copy
    # was cut for a refused instantiation, and the per-unit passes would only read the
    # same fault back as a second diagnostic from inside a body.
    constraint_violations: int = field(default=0, init=False)
    _refused: Set = field(default_factory=set, init=False, repr=False)
    # The error codes each call-site refusal offered, by key (#1070): a later site of the
    # same key offers them to its own unit's reporter again.
    _refusal_codes: Dict[object, Counter] = field(default_factory=dict, init=False,
                                                  repr=False)

    _monomorphize_depth: int = field(default=0, init=False, repr=False)

    _substitutor: TypeSubstitutor | None = field(default=None, init=False, repr=False)
    _type_monomorphizer: TypeMonomorphizer | None = field(default=None, init=False, repr=False)
    _function_monomorphizer: FunctionMonomorphizer | None = field(default=None, init=False, repr=False)

    @property
    def substitutor(self) -> TypeSubstitutor:
        """Lazy-initialize and return the type substitutor."""
        if self._substitutor is None:
            self._substitutor = TypeSubstitutor(self)
        return self._substitutor

    @property
    def type_monomorphizer(self) -> TypeMonomorphizer:
        """Lazy-initialize and return the type monomorphizer."""
        if self._type_monomorphizer is None:
            self._type_monomorphizer = TypeMonomorphizer(self)
        return self._type_monomorphizer

    @property
    def function_monomorphizer(self) -> FunctionMonomorphizer:
        """Lazy-initialize and return the function monomorphizer."""
        if self._function_monomorphizer is None:
            self._function_monomorphizer = FunctionMonomorphizer(self)
        return self._function_monomorphizer

    def _validate_type_constraints(
        self,
        type_params: Tuple,
        type_args: Tuple[Type, ...],
        key: object = None,
        template_file: str | None = None,
        error_params: Dict[str, object] | None = None,
        site: Tuple[Optional[Span], str | None] | None = None,
        report: bool = True,
    ) -> bool:
        """Validate perk constraints on type arguments. False when one refused (#579).

        ONE check for every kind of type parameter (#797). A trailing PACK parameter
        binds every argument from its position on, and each element is judged against
        the pack's constraints with the pack wording (CE2090); a leading parameter
        binds one argument (CE4006). A refusal of either kind cuts no copy.

        `key` names the instantiation in `sites` and in the refusal record: a refused
        instantiation is reported ONCE, at the first site that named it, and every later
        reach -- a field of another instance, a copy's body -- answers False silently.
        `template_file` is where the constraint is declared, for the note.

        `error_params` maps each type parameter that stands in an `E` position of the
        template to that position's span (`error_types.error_parameters`). Its argument
        must be an error type (E3), and a refusal is CE2084 at the instance, with a note
        at the template.
        """
        if key is not None and key in self._refused:
            return False

        if site is not None:
            span, filename = site
        else:
            span, filename = (self.sites.get(key, (None, None)) if key is not None
                              else (None, None))
        valid = (self._error_arguments_hold(type_params, type_args, error_params or {},
                                            span, filename, template_file)
                 if report else True)
        for position, param in enumerate(type_params):
            if self.constraint_validator is None:
                break
            if not (isinstance(param, BoundedTypeParam) and param.constraints):
                continue
            note = (getattr(param, "loc", None), template_file)
            if param.is_pack:
                bound = list(enumerate(type_args[position:]))
            else:
                bound = [(None, type_args[position])] if position < len(type_args) else []
            for pack_index, arg in bound:
                if report:
                    held = self.constraint_validator.validate_all_constraints(
                        param, arg, span, filename, note=note, pack_index=pack_index)
                else:
                    held = all(self.constraint_validator.satisfies(arg, name)
                               for name in param.constraints)
                if not held:
                    valid = False
        if not report:
            return valid
        if not valid:
            self.constraint_violations += 1
            if key is not None:
                self._refused.add(key)
        return valid

    def check_call_constraints(self, params, args, key, span, filename, template_file,
                               report: bool, *, reporter) -> bool:
        """The constraints of the type arguments of one site, from the typecheck pass.

        Four sites: a method-level type argument (#1191), and in a template check a
        generic call or function value, a written generic instance and an inferred
        constructor (#1070). The check a free function takes. `filename` is the file of
        the site and `template_file` the file of the constraint. Without `report` the
        answer is the same and nothing is emitted or recorded: the inferring half of the
        typecheck pass asks it too.

        A refusal goes to `reporter`, the reporter of the unit that holds the site: that
        unit then knows a diagnostic was given (a `match` over the refused call reads
        it, and the template check refuses its template), and the diagnostic takes its
        place in the unit's source order. A key is reported ONCE, at its first site. A
        later site, in any unit, offers the same errors to its own reporter again and
        records nothing, so it knows the call is refused and says nothing twice.
        """
        if report and key is not None and self.was_refused(key):
            if key not in self._refusal_codes:
                raise_internal_error("CE0015", message=(
                    "a call site meets a refusal that no call site reported"))
            reporter.offer_again(self._refusal_codes[key])
            return False
        validator = self.constraint_validator
        before = Counter(reporter.errors_offered)
        with (validator.reporting_to(reporter) if validator is not None
              else nullcontext()):
            held = self._validate_type_constraints(
                params, args, key=key, template_file=template_file,
                site=(span, filename), report=report)
        if report and not held and key is not None:
            self._refusal_codes[key] = reporter.errors_offered - before
        return held

    def _error_arguments_hold(self, type_params, type_args, error_params, span,
                              filename, template_file) -> bool:
        """E3 at one instance: every argument in an `E` position is an error type."""
        if not error_params or self.enum_table is None or self.struct_table is None:
            return True
        from sushi_lang.semantics.error_types import reject_non_error_type
        valid = True
        for position, param in enumerate(type_params):
            name = getattr(param, "name", param)
            if (name not in error_params or getattr(param, "is_pack", False)
                    or position >= len(type_args)):
                continue
            note = (f"the template uses the type parameter '{name}' as an error type here",
                    error_params[name], template_file)
            if reject_non_error_type(self.reporter, type_args[position], span,
                                     self.struct_table.by_name, self.enum_table.by_name,
                                     filename=filename, note=note):
                valid = False
        return valid

    def error_arguments_hold(self, type_params, type_args, error_params, site_key,
                             template_file) -> bool:
        """E3 at a copy that no constraint check reads: a generic-target extension's.

        The type instance that cuts the copy is the instance: the diagnostic is at the
        site that named it. A refusal stops the analysis, as a refused constraint does.
        """
        span, filename = self.sites.get(site_key, (None, None))
        if self._error_arguments_hold(type_params, type_args, error_params, span,
                                      filename, template_file):
            return True
        self.constraint_violations += 1
        return False

    def was_refused(self, key: object) -> bool:
        """A constraint refused this instantiation, and it was reported one time."""
        return key in self._refused

    def template_file(self, kind: str, name: str) -> str | None:
        """The file that declares the generic template `name`, when the tables know it."""
        table = getattr(self.tables, f"generic_{kind}s", None)
        files = getattr(table, "files", None) or {}
        return files.get(name)

    def template_span(self, kind: str, name: str):
        """Where the generic template `name` is declared, when the tables know it."""
        table = getattr(self.tables, f"generic_{kind}s", None)
        spans = getattr(table, "spans", None) or {}
        return spans.get(name)

    # Ceiling on nested type monomorphization. Real programs stay well under this
    # (a deeply nested Result<Maybe<HashMap<...>>> is only a handful of levels);
    # exceeding it means the instantiation is growing without bound.
    MONOMORPHIZE_MAX_DEPTH = 128

    @contextmanager
    def _monomorphize_depth_guard(self, type_name: str) -> Iterator[None]:
        """Bound recursive type monomorphization."""
        self._monomorphize_depth += 1
        try:
            if self._monomorphize_depth > self.MONOMORPHIZE_MAX_DEPTH:
                from sushi_lang.semantics.generics.type_display import display_type_name
                er.emit(self.reporter, er.ERR.CE0122, None,
                        name=display_type_name(type_name))
                raise MonomorphizationDepthExceeded(type_name)
            yield
        finally:
            self._monomorphize_depth -= 1

    def monomorphize_all(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic enum instantiations."""
        return self.type_monomorphizer.monomorphize_all_enums(generic_enums, instantiations)

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters."""
        return self.type_monomorphizer.monomorphize_enum(generic, type_args)

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations."""
        return self.type_monomorphizer.monomorphize_all_structs(generic_structs, instantiations)

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters."""
        return self.type_monomorphizer.monomorphize_struct(generic, type_args)

    def reached_instances(self):
        """Every published instance a substitution reached, keyed (base, args) per kind (#577)."""
        return self.type_monomorphizer.reached_instances()

    def monomorphize_function(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> Optional['FuncDef']:
        """Create concrete function from generic definition. None when a constraint refused."""
        return self.function_monomorphizer.monomorphize_function(generic, type_args)

    def monomorphize_all_functions(
        self,
        function_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
        program_or_units
    ) -> None:
        """Monomorphize all detected function instantiations."""
        self.function_monomorphizer.monomorphize_all_functions(
            function_instantiations, program_or_units
        )

    def collect_from_extension_body(self, extend_def) -> Set[Tuple[str, Tuple[Type, ...]]]:
        """Function instantiations in one monomorphized extension body (#392)."""
        return self.function_monomorphizer.collect_from_extension_body(extend_def)

    def collect_from_perk_method_body(self, target_type, method,
                                      filename: Optional[str] = None
                                      ) -> Set[Tuple[str, Tuple[Type, ...]]]:
        """The same, for one method of a monomorphized perk implementation.

        `filename` is the file of the template, where a nested instantiation's site is.
        """
        return self.function_monomorphizer.collect_from_perk_method_body(
            target_type, method, filename)
