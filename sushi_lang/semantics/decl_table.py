"""The declaration table: what the declaration round of the backend reads (docs/design/ir.md 6.3).

`declarations` makes one frozen `DeclTable` for each program, at the hand-off to the backend.
It is the one reader of a declaration of the AST after the analysis: the symbol of each
function, method and global, the types of each prototype, the folded value of each
initializer, and the `DeclId` of each body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Mapping, Optional, overload

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import (
    ConstDef, ExtendDef, ExtendWithDef, FuncDef, Param, Program, VarDef,
)
from sushi_lang.semantics.const_eval import ConstantEvaluator, allocates_nothing
from sushi_lang.semantics.generics.name_mangling import extension_decl_symbol, method_symbol
from sushi_lang.semantics.library_templates import impl_method_symbol
from sushi_lang.semantics.param_modes import receiver_mode
from sushi_lang.semantics.passes.collect.perks import _get_type_name
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.stdlib_registry import named_methods, stdlib_imports
from sushi_lang.semantics.type_resolution import require_named_type
from sushi_lang.semantics.typesys import Type, UnknownType
from sushi_lang.semantics.unit_symbols import (
    declaring_unit, function_symbol, global_symbol, mangle_unit_symbol,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.const_eval import ConstantValue
    from sushi_lang.semantics.namespaces import NamespaceTable
    from sushi_lang.semantics.shir.lowering import LoweredProgram
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.units import Unit


@dataclass(frozen=True, slots=True)
class FunctionDecl:
    """A free function of a unit, a monomorphized instance included."""
    name: str
    unit: Optional[str]
    symbol: str
    is_public: bool
    params: tuple[tuple[str, Type], ...]
    ret: Optional[Type]
    err_type: Optional[Type]
    written_channel: Optional[bool]
    resolved_result: Optional[Type]
    body: Optional[DeclId]


@dataclass(frozen=True, slots=True)
class MethodDecl:
    """An extension method, a static, or a method of a perk implementation."""
    name: str
    symbol: str
    link_name: str
    receiver: Optional[Type]
    by_pointer: bool
    params: tuple[tuple[str, Type], ...]
    ret: Optional[Type]
    err_type: Optional[Type]
    written_channel: Optional[bool]
    home_unit: Optional[str]
    body: Optional[DeclId]


@dataclass(frozen=True, slots=True)
class GlobalDecl:
    """A constant or a `var` of a unit."""
    name: str
    unit: Optional[str]
    symbol: str
    data_name: str
    ty: Optional[Type]
    is_var: bool
    link_symbol: Optional[str]
    empty: bool
    value: Optional[ConstantValue]
    loc: Optional[Span]


@dataclass(frozen=True, slots=True)
class UnitDecls:
    """The declarations of one unit, in the order of its module."""
    functions: tuple[FunctionDecl, ...]
    methods: tuple[MethodDecl, ...]
    globals: tuple[GlobalDecl, ...]
    perk_symbols: tuple[str, ...]
    stdlib_imports: tuple[str, ...]
    method_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class DeclTable:
    """The declarations of one program."""
    units: Mapping[str, UnitDecls]
    copies: tuple[MethodDecl, ...]
    shipped_perks: tuple[MethodDecl, ...]
    shipped_methods: tuple[MethodDecl, ...]

    def unit(self, name: str) -> Optional[UnitDecls]:
        """The declarations of the unit `name`, or None for a unit with no AST."""
        return self.units.get(name)


def declarations(units: Iterable[Unit], tables: SymbolTables, shir: LoweredProgram, *,
                 copies: Iterable[ExtendDef], shipped_methods: Iterable[ExtendDef],
                 shipped_perks: Iterable[ExtendWithDef]) -> DeclTable:
    """The declaration table of the program of `units` (ir.md 6.3)."""
    builder = _Builder(tables, shir)
    shipped: list[MethodDecl] = []
    for impl in shipped_perks:
        shipped.extend(builder.shipped_perk_methods(impl))
    return DeclTable(
        units={unit.name: builder.unit(unit.name, unit.ast) for unit in units
               if unit.ast is not None},
        copies=tuple(builder.extension(ext, None) for ext in copies),
        shipped_perks=tuple(shipped),
        shipped_methods=tuple(builder.extension(ext, None) for ext in shipped_methods))


def _module_order(functions: Iterable[FuncDef]) -> list[FuncDef]:
    """The written functions in source order, then the instances by name, never in the
    order of the copy fixpoint (ir.md 8.2)."""
    concrete = [fn for fn in functions if not fn.type_params]
    return ([fn for fn in concrete if not fn.is_synthesized]
            + sorted((fn for fn in concrete if fn.is_synthesized), key=lambda fn: fn.name))


class _Builder:
    """The reads of one table: the symbol tables, the SHIR program and the evaluator."""

    def __init__(self, tables: SymbolTables, shir: LoweredProgram) -> None:
        self.tables = tables
        self.shir = shir

    def body(self, decl: object) -> Optional[DeclId]:
        lowered = self.shir.body_of(decl)
        return None if lowered is None else lowered.fn_id

    def unit(self, name: str, program: Program) -> UnitDecls:
        methods = [self.extension(ext, name) for ext in program.extensions]
        perk_symbols: list[str] = []
        for impl in program.perk_impls:
            for method in impl.methods:
                methods.append(self.perk_method(
                    impl.target_type, method, _typed_params(method.params), method.ret,
                    method.err_type, self.body(method)))
            type_name = _get_type_name(impl.target_type)
            if type_name is not None:
                perk_symbols.extend(impl_method_symbol(type_name, method.name)
                                    for method in impl.methods)
        return UnitDecls(
            functions=tuple(self.function(fn, name) for fn in _module_order(program.functions)),
            methods=tuple(methods),
            globals=tuple(self.global_of(const, name) for const in program.constants),
            perk_symbols=tuple(perk_symbols), stdlib_imports=stdlib_imports(program),
            method_names=named_methods(program))

    def function(self, fn: FuncDef, unit_name: str) -> FunctionDecl:
        unit = declaring_unit(fn, unit_name)
        return FunctionDecl(
            name=fn.name, unit=unit, symbol=function_symbol(fn.name, unit),
            is_public=fn.is_public, params=_function_params(fn), ret=fn.ret,
            err_type=fn.err_type, written_channel=fn.written_channel,
            resolved_result=fn.resolved_result, body=self.body(fn))

    def extension(self, ext: ExtendDef, unit_name: Optional[str]) -> MethodDecl:
        """An extension method or a static. A STATIC method has no receiver (#542)."""
        symbol = extension_decl_symbol(ext, unit_name)
        receiver = ext.target_type if ext.target_type and not ext.is_static else None
        return MethodDecl(
            name=ext.name, symbol=symbol, link_name=ext.link_symbol or symbol,
            receiver=receiver, by_pointer=_by_pointer(receiver, ext.self_mode),
            params=_typed_params(ext.params), ret=ext.ret, err_type=ext.err_type,
            written_channel=ext.written_channel, home_unit=ext.home_unit, body=self.body(ext))

    def perk_method(self, target: Optional[Type], method: FuncDef,
                    params: tuple[tuple[str, Type], ...], ret: Optional[Type],
                    err_type: Optional[Type], body: Optional[DeclId]) -> MethodDecl:
        """A method of a perk implementation: the receiver mode and the channel of the
        method go with it (#327, ruling R1), and its symbol is the perk symbol of the target."""
        symbol = method_symbol(target, method.name, None)
        receiver = target if target else None
        return MethodDecl(
            name=method.name, symbol=symbol, link_name=symbol, receiver=receiver,
            by_pointer=_by_pointer(receiver, method.self_mode), params=params, ret=ret,
            err_type=err_type, written_channel=method.written_channel, home_unit=None,
            body=body)

    def shipped_perk_methods(self, impl: ExtendWithDef) -> list[MethodDecl]:
        """The methods of a perk implementation that a library ships, declared and never
        defined. A template is source text, so a type that the program declares arrives as
        an `UnknownType`; it resolves against the tables of the program."""
        target = self.resolved(impl.target_type)
        return [self.perk_method(
            target, method,
            tuple((p.name, self.resolved(p.ty)) for p in method.params if p.ty),
            self.resolved(method.ret), self.resolved(method.err_type), None)
            for method in impl.methods]

    @overload
    def resolved(self, ty: Type) -> Type: ...
    @overload
    def resolved(self, ty: Optional[Type]) -> Optional[Type]: ...

    def resolved(self, ty: Optional[Type]) -> Optional[Type]:
        if isinstance(ty, UnknownType):
            return require_named_type(ty, self.tables.structs.by_name, self.tables.enums.by_name)
        return ty

    def global_of(self, const: ConstDef, unit_name: str) -> GlobalDecl:
        """A constant or a `var`, with the folded value of its initializer (Q5). A compiled
        library's declaration is a name of the library unit, and its initializer reads that
        unit's scope (#1120)."""
        unit = const.scope_unit or unit_name
        sig = self.tables.constants.record_of(const, unit)
        ty = None if sig is None else sig.const_type
        is_var = isinstance(const, VarDef)
        link_symbol = const.link_symbol if isinstance(const, VarDef) else None
        empty = is_var and allocates_nothing(const.value)
        # A library constant's copy takes the symbol of the unit that declared it, so its
        # bytes never take the name of a function in the unit that holds the copy (#1101).
        data_unit = unit if is_var else (const.home_unit or unit)
        folds = link_symbol is None and not empty
        return GlobalDecl(
            name=const.name, unit=unit, symbol=global_symbol(const, unit),
            data_name=mangle_unit_symbol(data_unit, f".str_data.{const.name}"), ty=ty,
            is_var=is_var, link_symbol=link_symbol, empty=empty,
            value=self.folded(const, ty, unit) if folds and ty is not None else None,
            loc=const.loc)

    def folded(self, const: ConstDef, ty: Type, unit: str) -> Optional[ConstantValue]:
        """The value of an initializer, at the DECLARED type, which keeps a context-typed
        literal at its own width: `const u8 MAX = 200` folds to a u8, not an i32. Silent:
        the typecheck pass reported each fault."""
        tables = self.tables

        def namespaces_of(name: Optional[str]) -> Optional[NamespaceTable]:
            return None if name is None else tables.namespaces.get(name)

        evaluator = ConstantEvaluator(Reporter(), tables.constants, unit, namespaces_of,
                                      tables.structs, tables.enums, functions=tables.funcs)
        return evaluator.evaluate(const.value, ty, None)


def _by_pointer(receiver: Optional[Type], self_mode: Optional[str]) -> bool:
    """A `poke self` or a `peek self` receiver arrives by POINTER (#327); `nom self` and the
    unmarked one cross by value."""
    return receiver is not None and receiver_mode(self_mode).by_pointer


def _typed_params(params: Iterable[Param]) -> tuple[tuple[str, Type], ...]:
    return tuple((p.name, p.ty) for p in params if p.ty)


def _function_params(fn: FuncDef) -> tuple[tuple[str, Type], ...]:
    """The parameters of a free function. A parameter with no type is an internal error."""
    out = []
    for idx, p in enumerate(fn.params):
        if not isinstance(p, Param):
            raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] must be Param, "
                                                   f"got {type(p).__name__}")
        if not isinstance(p.name, str):
            raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] name must be str, "
                                                   f"got {type(p.name).__name__}")
        if p.ty is None:
            raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has no type")
        out.append((p.name, p.ty))
    return tuple(out)
