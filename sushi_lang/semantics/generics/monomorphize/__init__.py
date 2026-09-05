"""The monomorphize pass: every generic definition becomes concrete instances."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Dict, Iterator, Tuple, Set, TYPE_CHECKING
from dataclasses import dataclass, field

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.typesys import Type, EnumType, StructType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.internals.report import Reporter

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import FuncDef
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef, FunctionTable
    from sushi_lang.semantics.passes.collect.enums import EnumTable
    from sushi_lang.semantics.passes.collect.structs import StructTable
from sushi_lang.internals import errors as er

try:
    from sushi_lang.semantics.generics.constraints import ConstraintValidator
except ImportError:
    ConstraintValidator = None  # Graceful degradation if not available

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
        type_args: Tuple[Type, ...]
    ) -> None:
        """Validate perk constraints on type arguments (DRY helper)."""
        if self.constraint_validator is None:
            return

        for param, arg in zip(type_params, type_args, strict=False):
            if isinstance(param, BoundedTypeParam) and param.constraints:
                self.constraint_validator.validate_all_constraints(param, arg, None)

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
                er.emit(self.reporter, er.ERR.CE0122, None, name=type_name)
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
    ) -> 'FuncDef':
        """Create concrete function from generic definition."""
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

    def collect_from_perk_method_body(self, target_type, method
                                      ) -> Set[Tuple[str, Tuple[Type, ...]]]:
        """The same, for one method of a monomorphized perk implementation."""
        return self.function_monomorphizer.collect_from_perk_method_body(
            target_type, method)
