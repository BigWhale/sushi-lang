"""Perk definition and implementation collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.visibility import (
    VisibilityTable, reject_private_perk_contract)
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
    # (decision 11 of `docs/design/visibility.md`). A synthetic implementation and a
    # library manifest record have no declaring unit, so the answer may be None.
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

    def get_implementations(self, type_name: str) -> Set[str]:
        """Get all perks implemented by a type."""
        return self.by_type.get(type_name, set())

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

    def register_synthetic(self, type_name: str, perk_name: str) -> bool:
        """Register a synthetic perk implementation for primitives."""
        key = (type_name, perk_name)
        if key in self.implementations:
            return False  # Already registered (explicit or synthetic)

        self.implementations[key] = None  # type: ignore

        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(perk_name)

        if perk_name not in self.by_perk:
            self.by_perk[perk_name] = set()
        self.by_perk[perk_name].add(type_name)

        return True


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
        known_types: Optional[Set[Type]] = None,
        generic_perk_impls: Optional[GenericPerkImplTable] = None,
    ) -> None:
        """Initialize perk collector."""
        self.r = reporter
        # Which bare names are declared types, for reading a generic target's arguments:
        # `Box@(T)` and `Box@(Point)` are spelled identically and mean opposite things.
        self.known_types: Set[Type] = known_types if known_types is not None else set()
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
        perks = getattr(root, "perks", None)
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
        """
        perk_impls = getattr(root, "perk_impls", None)
        if isinstance(perk_impls, list):
            moved = []
            for impl in perk_impls:
                if isinstance(impl, ExtendWithDef):
                    if self._collect_perk_impl(impl):
                        moved.append(impl)
            if moved:
                moved_ids = {id(i) for i in moved}
                root.perk_impls[:] = [i for i in perk_impls
                                      if id(i) not in moved_ids]
                root.generic_perk_impls.extend(moved)

    # `Drop`'s contract is fixed, and both halves of it are checked: the RECEIVER must be
    # `poke self`, because a destructor writes, and the return must be blank, because a
    # destructor has nowhere to put a Result (CE4012).
    DROP_PERK = "Drop"

    def register_predefined_perks(self) -> None:
        """Register `Drop`, the perk that declares a resource (HANDLES.md ruling R2).

        A type that implements it owns something RAII must release, whatever its fields
        say. It ships with the compiler, beside the synthesized enums, so it needs no
        import -- `owns_resource` asks every program the question, so the answer has to
        exist in every program.
        """
        if self.perks.get(self.DROP_PERK) is not None:
            return
        drop = PerkDef(
            loc=None,
            name=self.DROP_PERK,
            methods=[PerkMethodSignature(
                name="drop", params=[], ret=BuiltinType.BLANK, self_mode="poke")],
            is_public=True,
        )
        self.perks.register(drop)
        self.perks.files[self.DROP_PERK] = None

    def register_synthetic_impls(self) -> None:
        """Auto-register synthetic perk implementations for primitive types."""
        hashable_primitives = [
            "i8", "i16", "i32", "i64",
            "u8", "u16", "u32", "u64",
            "f32", "f64", "bool", "string"
        ]

        hashable_perk = self.perks.get("Hashable")
        if hashable_perk:
            has_hash_method = any(
                method.name == "hash" and method.ret == BuiltinType.U64
                for method in hashable_perk.methods
            )

            if has_hash_method:
                for prim_type in hashable_primitives:
                    self.perk_impls.register_synthetic(prim_type, "Hashable")

    def _collect_perk_def(self, perk: PerkDef) -> None:
        """Collect perk definition and register in perk table."""
        name = getattr(perk, "name", None)
        if not isinstance(name, str):
            return

        # The definition sweep collected this same declaration already, and the unit it
        # belongs to is being collected now. One node twice is not two perks, so the
        # duplicate rule below must not read it as one (#487).
        if self.perks.get(name) is perk:
            return

        name_span: Optional[Span] = getattr(perk, "name_span", None) or getattr(perk, "loc", None)

        # Perks cannot be generic (CE4010). The grammar parses `perk Name<T>:`
        # and the AST builder stores the params, but nothing consumes them - so
        # before this check a generic perk was silently accepted and inert.
        if getattr(perk, "type_params", None):
            er.emit(self.r, ERR.CE4010, name_span, name=name)
            return

        # Variadic parameters are not allowed in perk methods (CE0115).
        # The pack half is unreachable today, but the guard must match its
        # documented contract and stay correct by construction (#246).
        for method in getattr(perk, "methods", []) or []:
            for p in getattr(method, "params", []) or []:
                if getattr(p, "is_variadic", False) or getattr(p, "is_pack", False):
                    er.emit(self.r, ERR.CE0115,
                            getattr(p, "name_span", None) or name_span,
                            context="a perk method")
                    break

        # A perk method that promises to RETURN a borrow is the same unsound shape as a
        # plain function returning one (CE2417, #314): the implementation would hand out a
        # view of its own frame. Perk method PARAMETERS stay legal -- that is the one
        # supported reference position.
        for method in getattr(perk, "methods", []) or []:
            reject_reference_in(self.r, getattr(method, "ret", None),
                                getattr(method, "ret_span", None)
                                or getattr(method, "name_span", None) or name_span,
                                ERR.CE2417)

        if self.perks.register(perk):
            self.perks.files[name] = self.current_unit_file
        else:
            prev = self.perks.get(name)
            prev_span = getattr(prev, "name_span", None) if prev else None
            diag = er.emit_with(self.r, ERR.CE4001, name_span, name=name)
            if prev_span is not None:
                diag.note("first defined here", prev_span, self.perks.files.get(name))
            diag.emit()
            return

    def _is_declared_type(self, name: str) -> bool:
        """Does this bare name in a target's argument position name a TYPE?"""
        return (name in self.perks.by_name
                or any(str(t) == name for t in self.known_types))

    def _reject_bad_drop_target(self, impl: ExtendWithDef, target_type: Optional[Type],
                                type_name: str, span: Optional[Span]) -> bool:
        """The two targets `Drop` refuses. Returns True when one was reported.

        A GENERIC target (CE4013) registers under a key carrying the type-parameter
        names, which no concrete instance matches, so the implementation would never
        fire and the resource would silently leak.

        A FOREIGN target (CE4012) is ruling R2b: only the unit that declares a type may
        say what releasing it means. A type with no declaration record yet is THIS unit's
        own -- the record is written after the collectors run -- or is synthesized, and
        both are allowed.
        """
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(target_type, GenericTypeRef):
            from sushi_lang.semantics.generics.extension_targets import (
                classify_extension_target)
            shape = classify_extension_target(
                target_type, self._is_declared_type)
            if shape.param_names:
                er.emit(self.r, ERR.CE4013, span, type=display_type(target_type))
                return True

        if self.visibility is None:
            return False
        for kind in ("struct", "enum"):
            origin = self.visibility.origin(kind, type_name)
            if origin is None or origin.unit_name is None:
                continue
            if origin.unit_name != self.current_unit_name:
                diag = er.emit_with(self.r, ERR.CE4012, span,
                                    type=type_name, owner=origin.unit_name)
                if origin.name_span is not None:
                    diag.note(f"'{type_name}' is declared here",
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
        """
        from sushi_lang.semantics.generics.extension_targets import (
            classify_extension_target)
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.passes.collect.functions import deep_type_params

        if not isinstance(target_type, GenericTypeRef):
            return False
        shape = classify_extension_target(target_type, self._is_declared_type)
        if shape.is_mixed:
            er.emit_with(self.r, ERR.CE2098,
                         getattr(impl, "target_type_span", None)
                         or getattr(impl, "perk_name_span", None),
                         target=display_type(target_type)) \
                .help("name every type parameter, or make every argument concrete -- "
                      "there is no partial specialization").emit()
            return True
        if not shape.param_names:
            return False

        for method in getattr(impl, "methods", []) or []:
            method.ret = deep_type_params(method.ret, shape.param_names)
            method.err_type = deep_type_params(
                getattr(method, "err_type", None), shape.param_names)
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
        perk_name = getattr(impl, "perk_name", None)
        if not isinstance(perk_name, str):
            return False

        # A `??` has no error channel in a BARE perk-impl body (CE0131, #398). A
        # declared `| E` IS the channel (ruling R1), so the reject does not apply
        # there -- the same three lines the extension arm runs.
        for method in getattr(impl, "methods", []) or []:
            if getattr(method, "err_type", None) is None:
                reject_try_in_body(self.r, getattr(method, "body", None), "a perk method")

        perk_name_span: Optional[Span] = getattr(impl, "perk_name_span", None) or getattr(impl, "loc", None)
        target_type: Optional[Type] = getattr(impl, "target_type", None)

        # `extend peek T with P` has the same problem as a reference extension target
        # (CE2420, #319): the implementation is registered against a type no receiver ever
        # resolves to, so it is unreachable.
        if reject_reference_in(self.r, target_type,
                               getattr(impl, "target_type_span", None)
                               or getattr(impl, "loc", None), ERR.CE2420):
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
            shadows_library = (owner is not None and owner in self.library_units
                               and self.current_unit_name not in self.library_units)
            if not shadows_library:
                er.emit(self.r, ERR.CE4002, getattr(impl, "loc", None),
                        type=type_name, perk=perk_name)
                return False
            previous = self.perk_impls.replace(impl, type_name,
                                               unit_name=self.current_unit_name)
            if previous is not None:
                self.shadowed_impls.append(previous)
        return False
