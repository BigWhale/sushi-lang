"""Perk definition and implementation collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.visibility import (
    VisibilityTable, library_clash_origin, record_declaration,
    reject_library_clash, reject_private_perk_contract, taken_by_a_library)
from sushi_lang.semantics.ast import (
    PerkDef, PerkMethodSignature, ExtendWithDef, FuncDef, Program)
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType, EnumType

from .utils import reject_reference_in, reject_try_in_body


@dataclass
class PerkTable:
    """Registry of all defined perks."""
    by_name: Dict[str, PerkDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    # The unit each perk was defined in. A PerkDef is an AST node and carries no file,
    # and a duplicate is reported while ANOTHER unit is being collected (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)

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
class GenericPerkImpl:
    """A perk implementation on a GENERIC target: `extend Box@(T) with Show`.

    A TEMPLATE, kept beside `GenericExtensionMethod` and read the same way. The
    implementation cannot be registered as it stands: the table is keyed by the
    instantiation the target names, and `Box@(T)` names none. One copy per
    instantiation of the base name is registered instead, after monomorphization.

    Every type in the methods' signatures is written in `TypeParameter`s, exactly as a
    generic extension method's is, so one substitution answers the whole signature.
    """
    base_type_name: str                    # "Box"
    type_params: Tuple[str, ...]           # ("T",) -- the names the target declares
    impl: ExtendWithDef                    # the template, signatures already converted
    unit_name: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class GenericPerkImplTable:
    """The generic-target perk implementations, by the base name of their target."""
    by_base: Dict[str, List[GenericPerkImpl]] = field(default_factory=dict)

    def add(self, template: GenericPerkImpl) -> None:
        self.by_base.setdefault(template.base_type_name, []).append(template)

    def templates(self, base_type_name: str) -> List[GenericPerkImpl]:
        return self.by_base.get(base_type_name, [])

    def __bool__(self) -> bool:
        return bool(self.by_base)


@dataclass
class PerkImplementationTable:
    """Tracks which types implement which perks."""
    implementations: Dict[Tuple[str, str], ExtendWithDef] = field(default_factory=dict)

    by_type: Dict[str, Set[str]] = field(default_factory=dict)

    by_perk: Dict[str, Set[str]] = field(default_factory=dict)

    # Which unit declared each implementation. Here and not on the collector, because
    # the question "may this implementation be replaced?" is asked of the table
    # (decision 11 of `docs/design/visibility.md`). A library manifest record has no
    # declaring unit, so the answer may be None.
    units: Dict[Tuple[str, str], Optional[str]] = field(default_factory=dict)

    def register(self, impl: ExtendWithDef, type_name: str,
                 *, unit_name: Optional[str] = None) -> bool:
        """Register an implementation. Returns False if duplicate."""
        key = (type_name, impl.perk_name)
        if key in self.implementations:
            return False  # Duplicate implementation

        self.implementations[key] = impl
        self.units[key] = unit_name

        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(impl.perk_name)

        if impl.perk_name not in self.by_perk:
            self.by_perk[impl.perk_name] = set()
        self.by_perk[impl.perk_name].add(type_name)

        return True

    def replace(self, impl: ExtendWithDef, type_name: str,
                *, unit_name: Optional[str] = None) -> Optional[ExtendWithDef]:
        """Take a pair over, and hand back the implementation it displaced.

        The sanctioned override: a consumer's `extend X with P` wins over a library's
        (CLAUDE.md's method-resolution rule). The displaced body is real Sushi in a real
        unit, so the caller has to drop it from that unit's AST.
        """
        key = (type_name, impl.perk_name)
        previous = self.implementations.get(key)
        self.implementations[key] = impl
        self.units[key] = unit_name
        return previous

    def owner(self, type_name: str, perk_name: str) -> Optional[str]:
        """The unit that declared this implementation, if a unit declared it."""
        return self.units.get((type_name, perk_name))

    def implements(self, type_name: str, perk_name: str) -> bool:
        """Check if a type implements a perk."""
        return (type_name, perk_name) in self.implementations

    def get(self, type_name: str, perk_name: str) -> Optional[ExtendWithDef]:
        """Get a specific perk implementation."""
        return self.implementations.get((type_name, perk_name))

    def get_method(self, target_type: 'Type', method_name: str) -> Optional['FuncDef']:
        """Get a specific perk method for a type."""
        type_name = _get_type_name(target_type)
        if type_name is None:
            return None

        perks = self.by_type.get(type_name, set())
        for perk_name in perks:
            impl = self.implementations.get((type_name, perk_name))
            if impl:
                for method in impl.methods:
                    if method.name == method_name:
                        return method

        return None


def _get_type_name(ty: Optional[Type]) -> Optional[str]:
    """Extract a string name from a Type for use in perk implementation tables."""
    if ty is None:
        return None

    if isinstance(ty, BuiltinType):
        return str(ty)

    if isinstance(ty, StructType):
        return ty.name

    if isinstance(ty, EnumType):
        return ty.name

    # A generic target is keyed by the instantiation it names, so it must mangle the way the
    # type table does. This built the name itself and joined on ',' where the interned name
    # joins on ', ': `extend Pair@(i32, string) with P` registered under `Pair<i32,string>`
    # while the receiver resolved to `Pair<i32, string>`, so every call was CE2008 -- the
    # single-argument case worked, which is what hid it. One authority for the name (#393).
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(ty, GenericTypeRef):
        return instantiation_key(ty.base_name, tuple(ty.type_args))

    return str(ty)


class PerkCollector:
    """Collector for perk definitions and implementations."""

    def __init__(
        self,
        reporter: Reporter,
        perks: PerkTable,
        perk_impls: PerkImplementationTable,
        is_declared_type: Callable[[str], bool],
        generic_perk_impls: Optional[GenericPerkImplTable] = None,
    ) -> None:
        """Initialize perk collector."""
        self.r = reporter
        # Which bare names are declared types, for reading a generic target's arguments:
        # `Box@(T)` and `Box@(Point)` are spelled identically and mean opposite things.
        self.is_declared_type = is_declared_type
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.perks = perks
        self.perk_impls = perk_impls
        # The generic-target templates. One copy per instantiation is registered in
        # `perk_impls` after monomorphization; nothing here can be keyed by a target
        # that names a type parameter.
        self.generic_perk_impls = (generic_perk_impls if generic_perk_impls is not None
                                   else GenericPerkImplTable())
        # Which unit is being collected, which units came from a source library, and
        # which unit registered each impl. Together they let a consumer impl replace a
        # library one silently -- the rule a binary library already follows
        # (docs/design/libraries.md section 7).
        self.current_unit_name: Optional[str] = None
        self.library_units: Set[str] = set()
        # Library impls a consumer replaced. Their bodies are real Sushi code in a real
        # unit, so unless they are dropped from that unit's AST the backend emits both
        # and the method symbol is defined twice. (A binary library has no such problem:
        # its body is weak_odr in the .slib object and the linker discards it.)
        self.shadowed_impls: List[ExtendWithDef] = []
        # Who declared what, for the perk-contract rule (CE4011).
        self.visibility: Optional[VisibilityTable] = None

    def collect_definitions(self, root: Program) -> None:
        """Collect all perk definitions from program AST."""
        perks = root.perks
        if isinstance(perks, list):
            for perk in perks:
                if isinstance(perk, PerkDef):
                    self._collect_perk_def(perk)

    def collect_implementations(self, root: Program) -> None:
        """Collect all perk implementations from program AST.

        A GENERIC target is re-filed out of `perk_impls` and into
        `generic_perk_impls`, for the same reason a generic extension is: every later
        walk over `perk_impls` -- the typecheck pass, the backend's declaration and
        definition loops, the fingerprint -- assumes a concrete `self`.

        A REFUSED implementation leaves both lists. A method that declares its own type
        parameters can match no contract, so every later reader would tell the same
        fault again in its own words -- which is what the CE4004 and CE2001 beside the
        real answer were (#704).
        """
        perk_impls = root.perk_impls
        if isinstance(perk_impls, list):
            moved, refused = [], []
            for impl in perk_impls:
                if not isinstance(impl, ExtendWithDef):
                    continue
                if self._reject_type_params_in_impl(impl):
                    refused.append(impl)
                elif self._collect_perk_impl(impl):
                    moved.append(impl)
            if moved or refused:
                dropped = {id(i) for i in (*moved, *refused)}
                root.perk_impls[:] = [i for i in perk_impls
                                      if id(i) not in dropped]
                root.generic_perk_impls.extend(moved)

    # `Drop`'s contract is fixed, and both halves of it are checked: the RECEIVER must be
    # `poke self`, because a destructor writes, and the return must be blank, because a
    # destructor has nowhere to put a Result (CE4012).
    DROP_PERK = "Drop"
    # `Hashable` is the contract of the derived `hash()`: every type the derive pass can
    # hash satisfies it with no implementation, and `extend T with Hashable` is the one
    # override of the derived hash (#696). The constraint check reads this name.
    HASHABLE_PERK = "Hashable"

    def _predefined_perks(self) -> List[PerkDef]:
        """The perks that ship with the compiler, public and importless."""
        return [
            PerkDef(
                loc=None,
                name=self.DROP_PERK,
                methods=[PerkMethodSignature(
                    name="drop", params=[], ret=BuiltinType.BLANK, self_mode="poke")],
                is_public=True,
            ),
            PerkDef(
                loc=None,
                name=self.HASHABLE_PERK,
                methods=[PerkMethodSignature(name="hash", params=[], ret=BuiltinType.U64)],
                is_public=True,
            ),
        ]

    def register_predefined_perks(self) -> None:
        """Register the two perks that ship with the compiler.

        `Drop` declares a resource (HANDLES.md ruling R2): a type that implements it owns
        something RAII must release, whatever its fields say. `Hashable` names the
        derived hash (#696): a type satisfies it when the derive pass can hash it, and
        an implementation is the override. Both stand beside the synthesized enums, so
        they need no import -- `owns_resource` and a `@(T: Hashable)` constraint ask
        every program the question, so the answer has to exist in every program.
        """
        for perk in self._predefined_perks():
            if self.perks.get(perk.name) is not None:
                continue
            self.perks.register(perk)
            self.perks.files[perk.name] = None

    def _collect_perk_def(self, perk: PerkDef) -> None:
        """Collect perk definition and register in perk table."""
        name = perk.name
        if not isinstance(name, str):
            return
        record_declaration(self.visibility, "perk", perk,
                           unit_name=self.current_unit_name,
                           filename=self.current_unit_file)

        # The definition sweep collected this same declaration already, and the unit it
        # belongs to is being collected now. One node twice is not two perks, so the
        # duplicate rule below must not read it as one (#487).
        if self.perks.get(name) is perk:
            return

        name_span: Optional[Span] = perk.name_span or perk.loc

        # Perks cannot be generic (CE4010). The grammar parses `perk Name<T>:`
        # and the AST builder stores the params, but nothing consumes them - so
        # before this check a generic perk was silently accepted and inert.
        if perk.type_params:
            er.emit(self.r, ERR.CE4010, name_span, name=name)
            return

        # Variadic parameters are not allowed in perk methods (CE0115).
        # The pack half is unreachable today, but the guard must match its
        # documented contract and stay correct by construction (#246).
        for method in perk.methods or []:
            for p in method.params or []:
                if p.is_variadic or p.is_pack:
                    er.emit(self.r, ERR.CE0115,
                            p.name_span or name_span,
                            context="a perk method")
                    break

        # A perk method that promises to RETURN a borrow is the same unsound shape as a
        # plain function returning one (CE2417, #314): the implementation would hand out a
        # view of its own frame. Perk method PARAMETERS stay legal -- that is the one
        # supported reference position.
        for method in perk.methods or []:
            reject_reference_in(self.r, method.ret,
                                method.ret_span
                                or method.name_span or name_span,
                                ERR.CE2417)

        if self.perks.register(perk):
            self.perks.files[name] = self.current_unit_file
        else:
            if self._reject_library_clash(name, name_span):
                return
            prev = self.perks.get(name)
            prev_span = prev.name_span if prev else None
            diag = er.emit_with(self.r, ERR.CE4001, name_span, name=name)
            if prev_span is not None:
                diag.note("first defined here", prev_span, self.perks.files.get(name))
            diag.emit()
            return

    def _reject_library_clash(self, name: str, name_span: Optional[Span]) -> bool:
        """CE3011 when a library already took this name PRIVATELY (#705).

        The perk follows the type's rule, because the substance already matches one: the
        table is flat and one perk name is one perk per program. CE4001's note pointed
        into a file the visibility rules say the consumer cannot see, and the user's two
        options -- rename, or ask the library to export the perk -- are the ones CE3011
        leads to. A PUBLIC library perk keeps CE4001: the consumer can read both
        declarations, which is the case that code was written for.
        """
        clash = library_clash_origin(
            self.visibility, "perk", name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is None or clash.is_public:
            return False
        reject_library_clash(self.r, clash, name_span, kind="perk", name=name,
                             filename=self.current_unit_file)
        return True

    def _reject_static_in_impl(self, impl: ExtendWithDef, perk_name: str) -> bool:
        """CE4014: a perk implementation may not declare a static method (#542, R1)."""
        refused = False
        for method in impl.methods or []:
            span = method.static_span
            if span is None:
                continue
            er.emit_with(self.r, ERR.CE4014, span,
                         perk=perk_name, method=method.name) \
                .help("declare it as a plain extension method on the type "
                      "('extend T static name(...)'); a perk contracts instance "
                      "methods only").emit()
            refused = True
        return refused

    def _reject_type_params_in_impl(self, impl: ExtendWithDef) -> bool:
        """CE4010: an implementation method declares no type parameters of its own.

        A perk cannot be generic, and a CONTRACT method has no `@(...)` slot in the
        grammar at all, so an implementation has no contract to match with one. The
        list was read by nothing: `@(U)` compiled and did nothing when the rest of the
        signature matched, and named an unknown type `U` when it did not (#704).

        The methods ride the shared `function_def`, which admits the list here for the
        reason it admits `public` and `static`: so this diagnostic can point at it.
        """
        perk_name = impl.perk_name if isinstance(impl.perk_name, str) else "?"
        refused = False
        for method in impl.methods or []:
            params = method.type_params or ()
            if not params:
                continue
            span = params[0].loc or method.name_span or method.loc
            er.emit_with(self.r, ERR.CE4010, span, name=perk_name) \
                .help("a perk contract declares no type parameters, so neither does "
                      "its implementation; a generic method is a plain extension "
                      "method ('extend T name@(U)(...)')").emit()
            refused = True
        return refused

    def _reject_bad_drop_target(self, impl: ExtendWithDef, target_type: Optional[Type],
                                type_name: str, span: Optional[Span]) -> bool:
        """The one target `Drop` refuses: a FOREIGN one. True when it was reported.

        CE4012 is ruling R2b: only the unit that declares a type may say what releasing
        it means. A type with no declaration record yet is THIS unit's own -- the record
        is written after the collectors run -- or is synthesized, and both are allowed.

        A GENERIC target reads its BASE name. `type_name` is the key the implementation
        registers under, and for a generic target that key carries the type arguments
        (`Crate<T>`, `Box<i32>`), which matches no declaration record -- so the rule
        would go silent on exactly the shape a generic `Drop` needs.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if self.visibility is None:
            return False
        declared_name = (target_type.base_name
                         if isinstance(target_type, GenericTypeRef) else type_name)
        for kind in ("struct", "enum"):
            origin = self.visibility.origin(kind, declared_name)
            if origin is None or origin.unit_name is None:
                continue
            if origin.unit_name != self.current_unit_name:
                diag = er.emit_with(self.r, ERR.CE4012, span,
                                    type=declared_name, owner=origin.unit_name)
                if origin.name_span is not None:
                    diag.note(f"'{declared_name}' is declared here",
                              origin.name_span, origin.filename)
                diag.emit()
                return True
        return False

    def _register_generic_template(self, impl: ExtendWithDef,
                                   target_type: Optional[Type]) -> bool:
        """Register a GENERIC-target implementation as a template. True when it is one.

        The signature of every method is rewritten in `TypeParameter`s over the names
        the target declares, exactly as `_collect_extension_def` does for a generic
        extension method, so one substitution answers the whole signature later.

        True also when the target was REFUSED. The implementation then leaves
        `perk_impls` and registers nowhere, which is what keeps one fault to one
        diagnostic -- the CE2098 arm above reads the same way.
        """
        from sushi_lang.semantics.generics.extension_targets import (
            classify_extension_target, reject_unwritable_target)
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.passes.collect.functions import deep_type_params

        if not isinstance(target_type, GenericTypeRef):
            return False
        shape = classify_extension_target(target_type, self.is_declared_type)
        if shape.is_mixed:
            er.emit_with(self.r, ERR.CE2098,
                         impl.target_type_span
                         or impl.perk_name_span,
                         target=display_type(target_type)) \
                .help("name every type parameter, or make every argument concrete -- "
                      "there is no partial specialization").emit()
            return True
        if reject_unwritable_target(self.r, shape, self.is_declared_type,
                                    impl.target_type_span or impl.perk_name_span):
            return True
        if not shape.param_names:
            return False

        for method in impl.methods or []:
            method.ret = deep_type_params(method.ret, shape.param_names)
            method.err_type = deep_type_params(
                method.err_type, shape.param_names)
            for param in method.params:
                param.ty = deep_type_params(param.ty, shape.param_names)

        self.generic_perk_impls.add(GenericPerkImpl(
            base_type_name=target_type.base_name,
            type_params=shape.param_names,
            impl=impl,
            unit_name=self.current_unit_name,
            filename=self.current_unit_file,
        ))
        return True

    def _collect_perk_impl(self, impl: ExtendWithDef) -> bool:
        """Collect one perk implementation. Answers True when it is a TEMPLATE.

        A template is re-filed by the caller: its target names a type parameter, so it
        registers nothing here and one copy per instantiation is registered later.
        """
        perk_name = impl.perk_name
        if not isinstance(perk_name, str):
            return False

        # A `??` has no error channel in a BARE perk-impl body (CE0131, #398). A
        # declared `| E` IS the channel (ruling R1), so the reject does not apply
        # there -- the same three lines the extension arm runs.
        for method in impl.methods or []:
            if method.err_type is None:
                reject_try_in_body(self.r, method.body, "a perk method")

        # A perk has no `Self` (HANDLES.md R7), so a contract cannot hold a
        # constructor. The grammar admits the marker here only so this diagnostic can
        # point at it (#542, ruling R1).
        if self._reject_static_in_impl(impl, perk_name):
            return False

        perk_name_span: Optional[Span] = impl.perk_name_span or impl.loc
        target_type: Optional[Type] = impl.target_type

        # `extend peek T with P` has the same problem as a reference extension target
        # (CE2420, #319): the implementation is registered against a type no receiver ever
        # resolves to, so it is unreachable.
        if reject_reference_in(self.r, target_type,
                               impl.target_type_span
                               or impl.loc, ERR.CE2420):
            return False

        type_name = _get_type_name(target_type)
        if type_name is None:
            return False

        if not self.perks.get(perk_name):
            er.emit(self.r, ERR.CE4003, perk_name_span, perk=perk_name)
            return False

        if perk_name == self.DROP_PERK and self._reject_bad_drop_target(
                impl, target_type, type_name, perk_name_span):
            return False

        # Ruling 3: a private perk keeps its CONTRACT, so another unit may not implement
        # it. The method it provides stays callable, which is the rest of the ruling.
        if reject_private_perk_contract(
                self.r, self.visibility, perk_name, perk_name_span,
                action="implement", current_unit=self.current_unit_name,
                filename=self.current_unit_file):
            return False

        if self._register_generic_template(impl, target_type):
            return True

        if not self.perk_impls.register(impl, type_name,
                                        unit_name=self.current_unit_name):
            owner = self.perk_impls.owner(type_name, perk_name)
            if not taken_by_a_library(owner, current_unit=self.current_unit_name,
                                      library_units=self.library_units):
                er.emit(self.r, ERR.CE4002, impl.loc,
                        type=type_name, perk=perk_name)
                return False
            previous = self.perk_impls.replace(impl, type_name,
                                               unit_name=self.current_unit_name)
            if previous is not None:
                self.shadowed_impls.append(previous)
        return False
