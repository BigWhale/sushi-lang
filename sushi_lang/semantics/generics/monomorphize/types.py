"""Enum and struct type monomorphization."""
from __future__ import annotations
from typing import Dict, Tuple, Set

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.internals.report import in_source_order
from sushi_lang.semantics.typesys import Type, EnumType, EnumVariantInfo, StructType, UnknownType
from sushi_lang.semantics.generics.explicit_type_args import reject_type_arg_arity
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.type_predicates import is_abstract_type
from sushi_lang.semantics.type_resolution import TypeResolver

from .order import types_in_site_order


class MonomorphizationDepthExceeded(Exception):
    """Raised when a generic type nests without bound during monomorphization."""


class TypeMonomorphizer:
    """Handles monomorphization of generic enum and struct types."""

    def __init__(self, monomorphizer):
        """Initialize type monomorphizer."""
        self.monomorphizer = monomorphizer
        # Each (generic, count) that `refuse_template_arity` refused at a written
        # template position, so the substituted instance does not report it again.
        self.template_arity_refused: Set[Tuple[str, int]] = set()

    def monomorphize_all_enums(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic enum instantiations."""
        self.monomorphizer.generic_enums = generic_enums

        concrete_enums: Dict[str, EnumType] = {}

        for base_name, type_args in types_in_site_order(
                instantiations, self.monomorphizer.sites):
            if base_name not in generic_enums:
                continue

            # An abstract instantiation still names an enclosing template's type params, so
            # there is nothing to monomorphize until a call site binds them. A bogus concrete
            # enum would sit in the table as a type that was never built.
            if self._is_abstract(type_args):
                continue

            generic = generic_enums[base_name]

            try:
                concrete = self.monomorphize_enum(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; the instantiation is infinitely
                # recursive. Skip it and let the reporter's error abort the build.
                continue

            concrete_enums[concrete.name] = concrete

        return concrete_enums

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters."""
        type_args = self._canonical_args(type_args)
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.cache:
            return self.monomorphizer.cache[cache_key]

        concrete_name = self._generate_concrete_name(generic.name, type_args)
        if self._refuse_arity("enum", generic, type_args, concrete_name):
            return EnumType(name=interned_name(generic.name, ("error",)), variants=())

        # A refused instantiation is built nowhere (#579, Ruling 4): not cached, not
        # published, so no template copy is ever cut for it. The shell keeps the caller
        # total; the analyzer stops before anything reads it.
        if not self.monomorphizer._validate_type_constraints(
                generic.type_params, type_args, key=concrete_name,
                template_file=self.monomorphizer.template_file("enum", generic.name)):
            return EnumType(name=interned_name(generic.name, ("error",)), variants=())

        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

        published = self._published(self.monomorphizer.enum_table, concrete_name)
        if published is not None:
            self.monomorphizer.cache[cache_key] = published
            return published

        # Tie-the-knot: publish an empty shell BEFORE substituting variant types, so a
        # self-referential field re-entering with this cache_key resolves by identity
        # instead of recursing forever. The shell is patched IN PLACE below. Sound because
        # the recursion passes through an opaque `Own<T>` pointer.
        concrete = EnumType(
            name=concrete_name,
            variants=(),
            generic_base=generic.name,
            generic_args=type_args
        )
        self.monomorphizer.cache[cache_key] = concrete
        self._publish(self.monomorphizer.enum_table, concrete, "enum")

        with self.monomorphizer._monomorphize_depth_guard(generic.name):
            concrete_variants = []
            for variant in generic.variants:
                concrete_associated_types = []
                for assoc_type in variant.associated_types:
                    concrete_type = self.monomorphizer.substitutor.substitute_type(
                        assoc_type, substitution
                    )
                    concrete_associated_types.append(concrete_type)

                concrete_variants.append(EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(concrete_associated_types)
                ))

        object.__setattr__(concrete, "variants", tuple(concrete_variants))

        return concrete

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations."""
        self.monomorphizer.generic_structs = generic_structs

        concrete_structs: Dict[str, StructType] = {}

        for base_name, type_args in types_in_site_order(
                instantiations, self.monomorphizer.sites):
            if base_name not in generic_structs:
                continue

            generic = generic_structs[base_name]

            try:
                concrete = self.monomorphize_struct(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; skip the infinitely recursive type.
                continue

            concrete_structs[concrete.name] = concrete

        return concrete_structs

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters."""
        type_args = self._canonical_args(type_args)
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.struct_cache:
            return self.monomorphizer.struct_cache[cache_key]

        concrete_name = self._generate_concrete_name(generic.name, type_args)
        if self._refuse_arity("struct", generic, type_args, concrete_name):
            return StructType(name=interned_name(generic.name, ("error",)), fields=())

        # A refused instantiation is built nowhere (#579, Ruling 4): not cached, not
        # published, so no template copy is ever cut for it. The shell keeps the caller
        # total; the analyzer stops before anything reads it.
        if not self.monomorphizer._validate_type_constraints(
                generic.type_params, type_args, key=concrete_name,
                template_file=self.monomorphizer.template_file("struct", generic.name)):
            return StructType(name=interned_name(generic.name, ("error",)), fields=())

        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

        published = self._published(self.monomorphizer.struct_table, concrete_name)
        if published is not None:
            self.monomorphizer.struct_cache[cache_key] = published
            return published

        # Tie-the-knot: publish an empty shell before substituting fields so a
        # self-referential field resolves to this same object by identity rather
        # than recursing forever (see monomorphize_enum for the full rationale).
        concrete = StructType(
            name=concrete_name,
            fields=(),
            generic_base=generic.name,
            generic_args=type_args
        )
        self.monomorphizer.struct_cache[cache_key] = concrete
        self._publish(self.monomorphizer.struct_table, concrete, "struct")

        with self.monomorphizer._monomorphize_depth_guard(generic.name):
            concrete_fields = []
            for field_name, field_type in generic.fields:
                concrete_type = self.monomorphizer.substitutor.substitute_type(
                    field_type, substitution
                )
                concrete_fields.append((field_name, concrete_type))

        object.__setattr__(concrete, "fields", tuple(concrete_fields))

        return concrete

    def _tables(self) -> Tuple[dict, dict]:
        """The struct and enum tables as dicts; empty on a unit-test path with no tables."""
        structs = self.monomorphizer.struct_table.by_name if self.monomorphizer.struct_table else {}
        enums = self.monomorphizer.enum_table.by_name if self.monomorphizer.enum_table else {}
        return structs, enums

    def _refuse_arity(self, kind: str, generic, type_args: Tuple[Type, ...],
                      key: str) -> bool:
        """CE2062 for an instantiation whose type-argument count is not the declared one.

        Every instantiation of a generic type enters here, the ones a written type names
        and the ones a template's substitution builds, so this is the one position that
        sees them all (#796). The refusal follows the constraint rule (#579, Ruling 4):
        reported ONCE, at the first site that named it, never built, and the analyzer
        stops after this pass, so no later reader repeats it.
        """
        mono = self.monomorphizer
        if len(type_args) == len(generic.type_params):
            return False
        if key in mono._refused:
            return True
        span, filename = mono.sites.get(key, (None, None))
        if span is None and (generic.name, len(type_args)) in self.template_arity_refused:
            # A substitution of a template position that `refuse_template_arity`
            # reported where it is written (#807).
            mono._refused.add(key)
            return True
        reject_type_arg_arity(
            mono.reporter, generic.name, generic, len(type_args), span, filename,
            declared_at=mono.template_span(kind, generic.name),
            declared_in=mono.template_file(kind, generic.name))
        mono._refused.add(key)
        mono.constraint_violations += 1
        return True

    def refuse_template_arity(self, units) -> None:
        """The written-template walk: CE2062 and CE2001 at every position INSIDE a template.

        The instantiate pass collects no type that still names a type parameter, so such
        a position has no site: a template with no instance was never checked, and an
        instance reported with no location (#807). The type-argument count does not
        depend on the type argument, and neither does whether a name names a type (#859),
        so both are checked here, where they are written, before any instance is made. A
        refusal stops the analysis after this pass, so no instance reports it again. A
        concrete type in a template is collected with its site and is not this walk's
        count.
        """
        from sushi_lang.semantics.ast_walk import is_written, signature_types
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.passes.types.utils import reject_unknown_template_name
        from sushi_lang.semantics.type_walk import walk_named_types

        for unit in units:
            if unit.ast is None or unit.provenance is not None:
                continue
            validator = self._template_validator(unit)
            # A generic-target extension or perk implementation is a template too. Its
            # TARGET is `reject_unwritable_target`'s, so the receiver site is not read.
            generic_targets = {id(decl) for decl in (*unit.ast.generic_extensions,
                                                     *unit.ast.generic_perk_impls)}
            for site in signature_types(unit.ast):
                if not is_written(site.decl):
                    continue
                if not (getattr(site.decl, "type_params", None)
                        or (id(site.decl) in generic_targets
                            and site.position != "receiver")):
                    continue
                known = self._template_params(site)
                for ty in walk_named_types(site.ty, through_declarations=False):
                    if validator is not None and reject_unknown_template_name(
                            validator, ty, site.span, known):
                        self.monomorphizer.constraint_violations += 1
                    elif isinstance(ty, GenericTypeRef) and self._is_abstract(ty.type_args):
                        self._refuse_written_arity(ty, site.span, str(unit.file_path))
            if validator is not None:
                self.monomorphizer.reporter.items.extend(
                    in_source_order(validator.reporter.items))

    def _template_validator(self, unit):
        """A validator in one unit's scope and file, for the names of its templates.

        None on a unit-test path with no whole-program tables. The unit's own reporter
        gives each diagnostic the unit's file, as the per-unit passes do.
        """
        tables = self.monomorphizer.tables
        if tables is None:
            return None
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.passes.types import TypeValidator

        reporter = Reporter(source=unit.read_source(), filename=str(unit.file_path),
                            provenance=unit.provenance)
        return TypeValidator(reporter, tables, current_unit_name=unit.name,
                             namespaces=tables.namespaces.get(unit.name))

    @staticmethod
    def _template_params(site) -> frozenset:
        """The type parameters a template position may name.

        The declaration's own and the method's own, and for a generic-target extension
        or perk implementation the names its target writes as parameters: `Box@(T)`
        and the element of `T[]` alike.
        """
        params = [*(getattr(site.decl, "type_params", None) or ()),
                  *(getattr(site.at, "type_params", None) or ())]
        from sushi_lang.semantics.type_walk import walk_named_types

        names = {param.name for param in params}
        target = getattr(site.decl, "target_type", None)
        names.update(ty.name for ty in walk_named_types(target, through_declarations=False)
                     if isinstance(ty, UnknownType))
        return frozenset(names)

    def _refuse_written_arity(self, ref, span, filename: str) -> None:
        """CE2062 for one written `@(...)` list in a template, when its count is wrong."""
        mono = self.monomorphizer
        for kind, generics in (("struct", mono.generic_structs), ("enum", mono.generic_enums)):
            generic = generics.get(ref.base_name)
            if generic is None:
                continue
            if reject_type_arg_arity(
                    mono.reporter, ref.base_name, generic, len(ref.type_args), span,
                    filename, declared_at=mono.template_span(kind, ref.base_name),
                    declared_in=mono.template_file(kind, ref.base_name)):
                self.template_arity_refused.add((ref.base_name, len(ref.type_args)))
                mono.constraint_violations += 1
            return

    def _is_abstract(self, type_args: Tuple[Type, ...]) -> bool:
        """Whether an argument still names an enclosing template's type parameter."""
        structs, enums = self._tables()
        return any(is_abstract_type(arg, structs, enums) for arg in type_args)

    def _canonical_args(self, type_args: Tuple[Type, ...]) -> Tuple[Type, ...]:
        """The arguments in the spelling the instantiate pass collects them in.

        A template's field can name a type outright (`Box@(Point)`), and the substitutor
        hands that name over as the `UnknownType` the AST builder wrote. The instance it
        builds then carries an unresolved payload under the same NAME as the resolved one
        the annotation scan interns, which is the two-depths collision CE0126 exists to
        catch. One resolution at the entry keeps one identity per name.
        """
        structs, enums = self._tables()
        return TypeResolver(structs, enums).resolve_type_args(type_args)

    @staticmethod
    def _published(table, name: str):
        """The table's instance of `name`, if one is interned already. Never rebuild a named type."""
        if table is None:
            return None
        return table.by_name.get(name)

    def _publish(self, table, concrete, kind: str) -> None:
        """Intern a new instance at creation, in the one place every producer passes (#577).

        The instantiate pass collects what annotations and calls SPELL. A `Box@(B)` field,
        a `Maybe@(B)` payload or a `Pair@(i32, B)` return is substituted HERE, and the
        `Box<string>` that comes out of it may be named nowhere else in the program. Left
        in the substitutor's cache alone it reached the derive pass through the outer
        instance's field as a type no table held. Publishing at creation is the
        worklist: the analyzer reads the tables back for the extension and perk copies.
        An abstract instance -- a method-level `U` still unbound while a generic-target
        template is cut per receiver -- is not a type and stays out.
        """
        if table is None or concrete.name in table.by_name:
            return
        if self._is_abstract(concrete.generic_args or ()):
            return
        table.by_name[concrete.name] = concrete
        table.order.append(concrete.name)
        self._stamp_template_origin(table, concrete, kind)
        self._derive_clone(concrete, kind)

    def _derive_clone(self, concrete, kind: str) -> None:
        """Derive clone() for the instance here, as the Result and Maybe seams do (#720).

        The derive pass walks both tables once, before typecheck, so an instance minted
        while a copy's body is substituted arrives behind it. A clone that waits for a
        later whole-table walk is an accident of loop order: a program that drives none
        never gets one, and `.clone()` on the instance is then refused with a reason
        that is not true (#721). The emitter is lazy, so the shell published above is
        all this needs; `Own`, `List` and `HashMap` keep their own method paths and the
        struct registration excludes them.

        The HASH deliberately does NOT move with it, and an instance that ends the
        analysis with neither is correct and not a gap (ruled on #730). The gate reads
        the type: `can_enum_be_hashed` walks the variants, and what is published here is
        an EMPTY SHELL -- the tie-the-knot step fills the variants afterwards -- so a
        registration made at this point would answer on no variants at all. A second
        visit after that step buys an ordering dependency in the monomorphizer to close
        a bucket with no symptom: 312 types of 1,915 programs end with neither, and
        every one of those programs compiles, links and runs.
        """
        from sushi_lang.semantics.generics.cloning import (
            register_enum_clone_method, register_struct_clone_method)

        derived = getattr(self.monomorphizer.enum_table, "derived", None)
        if derived is None:
            return
        if kind == "enum":
            register_enum_clone_method(concrete, derived)
        else:
            register_struct_clone_method(concrete, derived)

    def _stamp_template_origin(self, table, concrete, kind: str) -> None:
        """Where a diagnostic about this instance points: the TEMPLATE's declaration.

        An instance is spelled in no declaration -- `Box@(i32)` is minted from a field,
        a return or an annotation -- so it has a name and no source of its own, and a
        diagnostic that carries no span renders with the file name alone and no caret
        (#700). The template is the one declaration the reader can act on.
        """
        base = concrete.generic_base
        if base is None:
            return
        span = self.monomorphizer.template_span(kind, base)
        if span is None:
            return
        table.spans[concrete.name] = span
        table.files[concrete.name] = self.monomorphizer.template_file(kind, base)

    def reached_instances(self) -> Tuple[Dict[Tuple[str, Tuple[Type, ...]], EnumType],
                                         Dict[Tuple[str, Tuple[Type, ...]], StructType]]:
        """Every published instance a substitution reached, keyed as an instantiation."""
        structs, enums = self._tables()
        reached_enums = {key: ty for key, ty in self.monomorphizer.cache.items()
                         if enums.get(ty.name) is ty}
        reached_structs = {key: ty for key, ty in self.monomorphizer.struct_cache.items()
                           if structs.get(ty.name) is ty}
        return reached_enums, reached_structs

    def _generate_concrete_name(self, base_name: str, type_args: Tuple[Type, ...]) -> str:
        """Generate a unique name for a concrete generic type."""
        if not type_args:
            return base_name

        return interned_name(base_name, type_args)
