"""Generic function monomorphization."""
from __future__ import annotations
from typing import Dict, Iterator, Tuple, Set, Optional, TYPE_CHECKING
import copy
import typing
from collections import deque

from sushi_lang.semantics.ast import Expr
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.types import TypePack, type_param_substitution
from sushi_lang.semantics.typesys import Type

from sushi_lang.semantics.generics.local_bindings import (
    bind_locals, foreach_bindings, pattern_bindings, unbind_locals,
)

from .order import functions_in_site_order
from .transformer import pack_binding_for

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Block, Call, ExtendDef, FuncDef
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef


def extract_type_instantiations(
    ty: Optional[Type],
    instantiations: Set[Tuple[str, Tuple[Type, ...]]],
) -> None:
    """Every generic instantiation a SUBSTITUTED signature type names.

    A monomorphized type IS the instance and carries the base it came from, so the test
    at each node is `generic_base`; the traversal is `walk_named_types`, the one walk over
    a type. The recursion written here saw an iterator's element and nothing else -- not
    an array's element, not a reference's referent, not a function type's parameters --
    and a type reached only that way was never interned (#603).

    An instantiation already collected stops the walk of its arguments, which is what
    ends the walk of a type that holds an instance of itself.
    """
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.typesys import EnumType, StructType

    for inner in walk_named_types(ty):
        if not isinstance(inner, (StructType, EnumType)):
            continue
        base_name = inner.generic_base
        type_args = tuple(inner.generic_args or ())
        if not base_name or not type_args:
            continue
        entry = (base_name, type_args)
        if entry in instantiations:
            continue
        instantiations.add(entry)
        for arg in type_args:
            extract_type_instantiations(arg, instantiations)


# Every node kind of the `Expr` union: the statement walk hands each one to the
# expression collector, which `tests/unit/test_instantiation_collectors_are_total.py`
# holds total over the union.
EXPRESSION_KINDS = typing.get_args(Expr)


def let_annotations(block) -> Iterator[Type]:
    """Every `let` annotation in a block, nested blocks included.

    ONE statement walk for the two readers of a monomorphized body's local types: the
    function monomorphizer interns what a copy's `let`s name, and the analyzer's late
    seam does the same for an extension copy (#555). A `foreach` item's declared type
    is a local's annotation too.
    """
    from sushi_lang.semantics.ast import (
        Block, Let, If, While, Foreach, Match, Lambda, destructure_binders)

    if not isinstance(block, Block):
        return
    for stmt in block.statements:
        if isinstance(stmt, Let):
            if stmt.ty is not None:
                yield stmt.ty
            for binder in destructure_binders(stmt.targets):
                if binder.ty is not None:
                    yield binder.ty
            if isinstance(stmt.value, Lambda) and isinstance(stmt.value.body, Block):
                yield from let_annotations(stmt.value.body)
        elif isinstance(stmt, If):
            for _cond, arm in stmt.arms:
                yield from let_annotations(arm)
            yield from let_annotations(stmt.else_block)
        elif isinstance(stmt, While):
            yield from let_annotations(stmt.body)
        elif isinstance(stmt, Foreach):
            if stmt.item_type is not None:
                yield stmt.item_type
            yield from let_annotations(stmt.body)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                yield from let_annotations(arm.body)


def callable_error_parameters(ret, err_type, channel_span, params, body, body_span,
                              names) -> dict:
    """The type parameters `names` that a callable template writes in an `E` position (E3).

    A generic function and a generic extension method are read alike: the signature,
    with `T | E` read as the `Result@(T, E)` it is sugar for, each parameter, and every
    `let` in the body. A `let` has no span on this path, so its note is at `body_span`.
    """
    from sushi_lang.semantics.error_types import as_written_result, error_parameters

    written = [(as_written_result(ret, err_type), channel_span)]
    written += [(param.ty, param.type_span) for param in params]
    written += [(ty, body_span) for ty in let_annotations(body)]
    return error_parameters(written, set(names))


class FunctionMonomorphizer:
    """Handles monomorphization of generic functions."""

    def __init__(self, monomorphizer):
        """Initialize function monomorphizer."""
        self.monomorphizer = monomorphizer
        # The unit whose body the nested-call walk is inside. A name in a template
        # body binds in the DEFINITION's unit (D4's rule, at home), so the walk
        # resolves a nested generic call against the enclosing generic's unit.
        self._asking_unit = None
        # And the file the walked body is written in: a nested instantiation records its
        # site there, so a constraint refusal of it has a location (#1070).
        self._asking_file: Optional[str] = None
        # And the key of the instance whose copy is walked: a nested instantiation names
        # it as its parent, so a refusal finds the written site that started the chain.
        # The key of a function copy is a call key. The key of an extension or a perk
        # method copy is the interned name of its target type.
        self._asking_key: object = None

    def _generic_def(self, unit_name, func_name):
        """The generic `func_name` means inside `unit_name`: own unit, then flat.

        `generic_funcs` is the collect pass's table on the compiler path and a plain
        dict on unit-test paths; both answer, the dict with its one flat view.
        """
        table = self.monomorphizer.generic_funcs
        if table is None:
            return None
        by_unit = getattr(table, "by_unit", None)
        if by_unit is not None:
            return table.lookup(func_name, unit_name)
        return table.get(func_name)

    def _constraints_hold(self, generic, params, args) -> bool:
        """The constraint check for one function instantiation, at the call that named it.

        `params` are every type parameter and `args` every argument; a trailing pack
        parameter binds the rest of `args`, and the check judges each element (#797).
        """
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        return self.monomorphizer._validate_type_constraints(
            params, args,
            key=("fn", instantiation_key(generic.name, tuple(args))),
            template_file=getattr(generic, "filename", None),
            error_params=callable_error_parameters(
                generic.ret, generic.err_type, generic.err_span or generic.ret_span,
                generic.params, generic.body, generic.name_span,
                {param.name for param in params}))

    def build_substitution(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> "Dict[str, Type | TypePack] | None":
        """Build the type-parameter -> binding substitution map for a generic.

        None when a constraint refused the instantiation (#579): no copy is cut.
        """
        tps = list(generic.type_params)

        pack_indices = [i for i, tp in enumerate(tps) if tp.is_pack]

        if not pack_indices:
            if len(type_args) != len(tps):
                raise ValueError(
                    f"Type argument count mismatch: {generic.name} expects "
                    f"{len(tps)} args, got {len(type_args)}"
                )
        else:
            if len(pack_indices) > 1:
                raise ValueError(
                    f"{generic.name} declares {len(pack_indices)} pack type-parameters; "
                    f"at most one is allowed"
                )

            k = pack_indices[0]
            if k != len(tps) - 1:
                raise ValueError(
                    f"{generic.name} declares a pack type-parameter that is not the "
                    f"last type-parameter (at index {k} of {len(tps)})"
                )

            if len(type_args) < k:
                raise ValueError(
                    f"Type argument count mismatch: {generic.name} expects at least "
                    f"{k} args, got {len(type_args)}"
                )

        if not self._constraints_hold(generic, tps, type_args):
            return None

        return type_param_substitution(generic, type_args)

    def _grows_without_end(self, generic, type_args) -> bool:
        """CE0151 when this instantiation makes its chain of copies grow without end."""
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        from sushi_lang.semantics.generics.type_display import display_type
        unit_name = getattr(generic, "unit_name", None)
        instance = f"{generic.name}@({', '.join(display_type(arg) for arg in type_args)})"
        return self.monomorphizer.refuses_growth(
            ("fn", instantiation_key(generic.name, tuple(type_args))),
            ("fn", unit_name, generic.name),
            tuple(type_args), "function", generic.name, instance)

    def monomorphize_function(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> Optional['FuncDef']:
        """Create concrete function from generic definition. None when a constraint refused,
        or when the copy makes its chain grow without end (CE0151)."""
        cache_key = (getattr(generic, "unit_name", None), generic.name, type_args)
        if cache_key in self.monomorphizer.func_cache:
            return self.monomorphizer.func_cache[cache_key]

        if self._grows_without_end(generic, type_args):
            return None
        substitution = self.build_substitution(generic, type_args)
        if substitution is None:
            return None

        concrete_func, pack_param_fanout = self._cut(generic, substitution)

        # A trailing pack type-param passes its arity, so the symbol is distinct per pack
        # size and cannot collide with a regular generic of the same base.
        type_params = generic.type_params or []
        has_pack = bool(type_params) and type_params[-1].is_pack
        if has_pack:
            pack_arity = len(type_args) - (len(type_params) - 1)
            mangled_name = mangle_function_name(
                generic.name, type_args, pack_arity=pack_arity
            )
        else:
            mangled_name = mangle_function_name(generic.name, type_args)

        self.monomorphizer.monomorphized_functions[mangled_name] = (
            getattr(generic, "unit_name", None), generic.name, type_args)

        # Unroll `expand(...)` into ordinary statements, so no later pass ever sees an
        # Expand: each element's copy is straight-line and names its element parameter.
        if pack_param_fanout:
            from sushi_lang.semantics.generics.monomorphize.unroll import unroll_expands
            concrete_func.body = unroll_expands(concrete_func.body, pack_param_fanout)

        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        # The key names the copy as the parent of each copy its body names: here, and in
        # the typecheck pass for a call-site method copy (`instance_growth.py`).
        concrete_func.instance_key = ("fn", instantiation_key(generic.name, tuple(type_args)))
        self._collect_nested_instantiations(
            concrete_func.body, concrete_func.params, generic, concrete_func.instance_key)
        concrete_func.name = mangled_name

        self._collect_fn_value_instantiations(
            concrete_func.body, getattr(generic, "unit_name", None),
            file=generic.filename, functions=[concrete_func])

        self.monomorphizer.func_cache[cache_key] = concrete_func

        return concrete_func

    def cut_body(self, generic: 'GenericFuncDef',
                 substitution: "Dict[str, Type | TypePack]") -> 'FuncDef':
        """The template with `substitution` put through its signature and its body.

        It does not mangle, register, collect nested instantiations or unroll: the
        template check cuts its check copy with the opaque parameters here (#1070), and
        `monomorphize_function` adds its own bookkeeping to the same cut.
        """
        return self._cut(generic, substitution)[0]

    def _cut(self, generic: 'GenericFuncDef',
             substitution: "Dict[str, Type | TypePack]") -> "Tuple[FuncDef, Dict[str, list]]":
        """The cut, and the element names each pack parameter fans out to."""
        # Substitute in parameter types. A pack-typed value-parameter fans out
        # into N concrete params (one per pack element, possibly zero); a normal
        # param yields exactly one concrete param identical to the legacy result.
        substitutor = self.monomorphizer.substitutor
        concrete_params = []
        pack_param_fanout: Dict[str, list] = {}
        for param in generic.params:
            expanded = substitutor.expand_pack_param(param, substitution)
            if pack_binding_for(param, substitution) is not None:
                pack_param_fanout[param.name] = [p.name for p in expanded]
            concrete_params.extend(expanded)

        concrete_ret = substitutor.substitute_type(
            generic.ret, substitution) if generic.ret else None

        concrete_body = substitutor.substitute_body(generic.body, substitution)

        # The channel is substituted like every other type in the signature. A copy
        # carries `err_type` through, so `fn f@(E)(T v) i32 | E` would otherwise reach
        # the backend with an unsubstituted type parameter in its error arm.
        # `generics/extensions.py` does the same for a method's channel.
        concrete_err = substitutor.substitute_type(
            generic.err_type, substitution) if getattr(generic, "err_type", None) else None

        from sushi_lang.semantics.channel import has_channel
        concrete_func = copy.copy(generic)
        concrete_func.written_channel = has_channel(generic)
        concrete_func.params = concrete_params
        concrete_func.ret = concrete_ret
        concrete_func.err_type = concrete_err
        concrete_func.body = concrete_body
        concrete_func.type_params = None  # No longer generic
        return concrete_func, pack_param_fanout

    def monomorphize_all_functions(
        self,
        function_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
        program_or_units
    ) -> None:
        """Monomorphize all detected function instantiations."""
        from sushi_lang.semantics.ast import Program

        if not self.monomorphizer.func_table:
            return

        is_single_file = isinstance(program_or_units, Program)
        target_program = program_or_units if is_single_file else None
        units = None if is_single_file else program_or_units

        sites = self.monomorphizer.sites
        worklist = deque(functions_in_site_order(function_instantiations, sites))
        processed = set()

        self.monomorphizer.pending_instantiations = set()

        while worklist:
            unit_name, func_name, type_args = worklist.popleft()

            if (unit_name, func_name, type_args) in processed:
                continue
            processed.add((unit_name, func_name, type_args))

            generic_func = self._generic_def(unit_name, func_name)
            if generic_func is None:
                continue

            concrete_func = self.monomorphize_function(generic_func, type_args)
            if concrete_func is None:
                continue

            # Extract enum/struct instantiations from the function signature
            # This ensures that Result<T>, Maybe<T>, and other generic return/param types
            # are properly monomorphized even if they weren't detected by InstantiationCollector
            signature_instantiations = set()

            if concrete_func.ret and concrete_func.err_type is not None:
                signature_instantiations.add(
                    ("Result", (concrete_func.ret, concrete_func.err_type)))

            extract_type_instantiations(concrete_func.ret, signature_instantiations)
            for param in concrete_func.params:
                extract_type_instantiations(param.ty, signature_instantiations)
            # The body's own annotations too: `let Box@(T) b` in the copy is a
            # `Box<string>` the substitutor built, and nothing else may name it (#555).
            # Unrecorded, it lived in the substitutor's cache alone, and every use of
            # the local was CE2008 on a type that was never interned.
            for annotation in let_annotations(concrete_func.body):
                extract_type_instantiations(annotation, signature_instantiations)

            # Each is published to its table at creation (`TypeMonomorphizer._publish`).
            for base_name, sig_type_args in signature_instantiations:
                if base_name in self.monomorphizer.generic_enums:
                    self.monomorphizer.monomorphize_enum(
                        self.monomorphizer.generic_enums[base_name], sig_type_args)
                elif base_name in self.monomorphizer.generic_structs:
                    self.monomorphizer.monomorphize_struct(
                        self.monomorphizer.generic_structs[base_name], sig_type_args)

            mangled_name = concrete_func.name

            # Per unit, not flat: two units' instances share the mangled base name and
            # each unit must keep its own body (#495).
            home_unit = getattr(generic_func, "unit_name", None)
            declared = (self.monomorphizer.func_table.by_unit.get(home_unit, {})
                        if home_unit is not None
                        else self.monomorphizer.func_table.by_name)
            if mangled_name in declared:
                continue

            # Which source this body is a copy of. Every copy carries the template's
            # spans, so the reporter tells a fault in the shared source once (#648), and
            # says nothing for a template whose check refused it (#1070).
            concrete_func.instance_of = generic_func.name
            from sushi_lang.semantics.generics.types import TemplateId
            concrete_func.template_id = TemplateId(home_unit, generic_func.name)
            concrete_func.pack_names = tuple(
                p.name for p in generic_func.params if p.is_pack)

            from sushi_lang.semantics.generics.synthesis import register_synthesized_function
            register_synthesized_function(
                self.monomorphizer.func_table,
                concrete_func,
                program=target_program if is_single_file else None,
                units=None if is_single_file else units,
                home_unit=home_unit,
                from_library_template=getattr(
                    generic_func, "is_library_template", False),
                origin=getattr(generic_func, "library_origin", None),
                defer_to=self.monomorphizer.late_bodies,
            )

            worklist.extend(functions_in_site_order(
                self.monomorphizer.pending_instantiations, sites))
            self.monomorphizer.pending_instantiations.clear()

    def _collect_nested_instantiations(
        self,
        body: 'Block',
        params: list,
        generic_func: 'GenericFuncDef',
        key: object = None,
    ) -> None:
        """Every generic call in one monomorphized function body, queued for monomorphization.

        The walk reads the SUBSTITUTED copy, as the extension walk does: a lambda's
        written `|T y|` and an explicit `f@(T)(...)` name the concrete type only there,
        and a template walk solved them against an unbound type parameter (#795).
        `key` is the instantiation of the copy.
        """
        var_types = {param.name: param.ty for param in params if param.ty is not None}
        saved = self._asking_unit, self._asking_file, self._asking_key
        self._asking_unit = getattr(generic_func, "unit_name", None)
        self._asking_file = generic_func.filename
        self._asking_key = key
        self._collect_block_instantiations(body, var_types)
        self._asking_unit, self._asking_file, self._asking_key = saved

    def _record_site(self, name: str, type_args, loc) -> None:
        """The first site that names a nested function instantiation (#579, #1070).

        A written site stays first. A new site records the instance of the walked copy as
        its parent.
        """
        if loc is None or self._asking_file is None:
            return
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        key = ("fn", instantiation_key(name, tuple(type_args)))
        if key in self.monomorphizer.sites:
            return
        self.monomorphizer.sites[key] = (loc, self._asking_file)
        if self._asking_key is not None:
            self.monomorphizer.parents[key] = self._asking_key

    def _collect_fn_value_instantiations(self, body: 'Block', unit_name: Optional[str],
                                         file: Optional[str] = None,
                                         **declarations) -> None:
        """Every generic function VALUE in one copy, queued for monomorphization (#1036).

        A value is solved by the declared type of its position, and in a template that
        type can name a type parameter. So the copy is walked, by the instantiate pass's
        own collector: one rule for every position, in a concrete body and in a copy.
        `declarations` is the copy as a `Program` field (`functions=`, `extensions=` or
        `perk_impls=`). An extension or perk copy carries no unit, so it reads the flat view.

        The same walk solves a generic CONSTRUCTOR in a position that states no type
        (#1150, #1152): `foreach(r in Feed(items, 0))` names `Feed@(i32)` only in the
        copy. The collector's inference interns that instance, and the late cut gives it
        its extension copies.
        """
        tables = self.monomorphizer.tables
        if tables is None:
            return
        namespaces = tables.namespaces.get(unit_name) if unit_name is not None else None
        if not self._holds_position_candidate(body, unit_name, namespaces):
            return
        from sushi_lang.semantics.ast import Program
        from sushi_lang.semantics.generics.instantiate import InstantiationCollector
        collector = InstantiationCollector(
            struct_table=tables.structs.by_name,
            enum_table=tables.enums.by_name,
            generic_structs=tables.generic_structs.by_name,
            generic_funcs=tables.generic_funcs.view_for(
                unit_name, getattr(namespaces, "scope", None)),
            func_table=tables.funcs.by_name,
            tables=tables,
            namespaces=namespaces,
            sites=self.monomorphizer.sites,
            current_file=file,
        )
        program = Program(uses=[], constants=[], structs=[], enums=[], perks=[],
                          functions=[], extensions=[], generic_extensions=[],
                          perk_impls=[], loc=getattr(body, "loc", None))
        for kind, decls in declarations.items():
            setattr(program, kind, decls)
        _, found = collector.run(program)
        for key in found:
            if key not in self.monomorphizer.func_cache:
                self.monomorphizer.pending_instantiations.add(key)

    def _holds_position_candidate(self, body: 'Block', unit_name: Optional[str],
                                  namespaces) -> bool:
        """The copy names a generic function, or a name behind an alias, as a VALUE, or
        it constructs a generic type."""
        from sushi_lang.semantics.ast import Call, DotCall, EnumConstructor, MemberAccess, Name
        from sushi_lang.semantics.ast_walk import walk_nodes
        tables = self.monomorphizer.tables
        generic_structs = tables.generic_structs.by_name
        generic_enums = tables.generic_enums.by_name
        callees: Set[int] = set()
        found = False

        def visit(node) -> bool:
            nonlocal found
            if found:
                return False
            if isinstance(node, Call) and isinstance(node.callee, Name):
                callees.add(id(node.callee))
                found = node.callee.id in generic_structs
            elif isinstance(node, EnumConstructor):
                found = node.enum_name in generic_enums
            elif isinstance(node, DotCall) and isinstance(node.receiver, Name):
                found = node.receiver.id in generic_enums or (
                    namespaces is not None and namespaces.is_namespace(node.receiver.id))
            elif isinstance(node, Name) and id(node) not in callees:
                found = self._generic_def(unit_name, node.id) is not None
            elif (isinstance(node, MemberAccess) and isinstance(node.receiver, Name)
                    and namespaces is not None):
                found = namespaces.is_namespace(node.receiver.id)
            return not found

        walk_nodes(body, visit)
        return found

    def collect_from_extension_body(self, extend_def: 'ExtendDef') -> Set[Tuple[str, Tuple[Type, ...]]]:
        """Function instantiations in one MONOMORPHIZED extension body (#392).

        A generic call whose argument types come from `self` is knowable only here: the
        template spells the receiver's type parameter, and the substituted copy is the
        first body in which `self.value` has a concrete type. The body is already
        substituted, so the walk runs with `self` bound to the concrete target.
        """
        var_types: Dict[str, Type] = {"self": extend_def.target_type}
        for param in extend_def.params:
            if param.ty is not None:
                var_types[param.name] = param.ty

        saved = getattr(self.monomorphizer, 'pending_instantiations', None)
        self.monomorphizer.pending_instantiations = set()
        saved_unit, saved_file = self._asking_unit, self._asking_file
        saved_key = self._asking_key
        self._asking_unit = None
        self._asking_file = extend_def.template_file
        self._asking_key = (getattr(extend_def, "instance_key", None)
                            or getattr(extend_def.target_type, "name", None))
        self._collect_block_instantiations(extend_def.body, var_types)
        self._asking_unit, self._asking_key = saved_unit, saved_key
        self._collect_fn_value_instantiations(extend_def.body, None, file=self._asking_file,
                                              extensions=[extend_def])
        self._asking_file = saved_file
        found = self.monomorphizer.pending_instantiations
        self.monomorphizer.pending_instantiations = saved if saved is not None else set()
        return found

    def collect_from_perk_method_body(self, target_type: Type, method,
                                      filename: Optional[str]
                                      ) -> Set[Tuple[str, Tuple[Type, ...]]]:
        """The same walk, for one monomorphized perk-implementation method.

        A perk method carries no target of its own -- the `extend X with P` header does
        -- so the receiver's type is handed in rather than read off the node.
        """
        var_types: Dict[str, Type] = {"self": target_type}
        for param in method.params:
            if param.ty is not None:
                var_types[param.name] = param.ty

        saved = getattr(self.monomorphizer, 'pending_instantiations', None)
        self.monomorphizer.pending_instantiations = set()
        saved_unit, saved_file = self._asking_unit, self._asking_file
        saved_key = self._asking_key
        self._asking_unit = None
        self._asking_file = filename
        self._asking_key = getattr(target_type, "name", None)
        self._collect_block_instantiations(method.body, var_types)
        self._asking_unit, self._asking_key = saved_unit, saved_key
        from sushi_lang.semantics.ast import ExtendWithDef
        file = self._asking_file
        self._asking_file = saved_file
        self._collect_fn_value_instantiations(
            method.body, None, file=file, perk_impls=[ExtendWithDef(
                target_type=target_type, perk_name="", methods=[method],
                loc=getattr(method, "loc", None))])
        found = self.monomorphizer.pending_instantiations
        self.monomorphizer.pending_instantiations = saved if saved is not None else set()
        return found

    def _collect_block_instantiations(
        self,
        body: 'Block',
        var_types: Dict[str, Type],
    ) -> None:
        """The statement walk over a SUBSTITUTED body, shared by functions and extensions.

        A statement that BINDS a name (a `let`, a `foreach`, a `match`) has an arm of its
        own, so the name is in scope where it is used. Every other statement is walked whole by
        the one node walk, so no statement kind can be missed: `println`, `print` and a
        rebind had no arm, and their generic calls were not collected (#1157).
        `tests/unit/test_monomorphize_statement_walk_is_total.py` is the gate.
        """
        from sushi_lang.semantics.ast import Let, Match, Foreach, Block, Lambda

        for stmt in body.statements:
            if isinstance(stmt, Let):
                if stmt.value is not None:
                    self._collect_from_expr(stmt.value, var_types)
                    if isinstance(stmt.value, Lambda) and isinstance(stmt.value.body, Block):
                        self._collect_block_instantiations(stmt.value.body, var_types)
                # A local is in scope for the calls after it, and a generic called with
                # one needs its type as a parameter's is needed (#555). The body is
                # substituted, so the annotation is the local's type as it stands.
                if stmt.ty is not None:
                    var_types[stmt.name] = stmt.ty
                if stmt.targets is not None:
                    self._bind_destructure(stmt, var_types)
            elif isinstance(stmt, Foreach):
                self._collect_from_expr(stmt.iterable, var_types)
                bound = bind_locals(var_types, foreach_bindings(
                    stmt, self._inferred(var_types), self._resolved))
                self._collect_block_instantiations(stmt.body, var_types)
                unbind_locals(var_types, bound)
            elif isinstance(stmt, Match):
                self._collect_from_expr(stmt.scrutinee, var_types)
                scrutinee_type = self._inferred(var_types)(stmt.scrutinee)
                for arm in stmt.arms:
                    bound = bind_locals(var_types, pattern_bindings(
                        arm.pattern, scrutinee_type, self.monomorphizer.generic_enums,
                        self._resolved))
                    self._collect_from_statement_nodes(arm.body, var_types)
                    unbind_locals(var_types, bound)
            else:
                self._collect_from_statement_nodes(stmt, var_types)

    def _collect_from_statement_nodes(self, root, var_types: Dict[str, Type]) -> None:
        """Every expression under a statement, through the one node walk.

        A nested block is walked as a block, in the scope it opens; an expression goes
        to the expression collector, which walks its own subtree.
        """
        from sushi_lang.semantics.ast import Block
        from sushi_lang.semantics.ast_walk import walk_nodes

        def visit(node) -> bool:
            if isinstance(node, Block):
                self._collect_block_instantiations(node, var_types)
                return False
            if isinstance(node, EXPRESSION_KINDS):
                self._collect_from_expr(node, var_types)
                return False
            return True

        walk_nodes(root, visit)

    def _inferred(self, var_types: Dict[str, Type]):
        """The type of an expression in this scope, or None when nothing can type it."""
        def infer(expr) -> Optional[Type]:
            inferrer = self._get_arg_inferrer(var_types)
            return inferrer.infer_expression_type(expr) if inferrer is not None else None
        return infer

    def _resolved(self, ty: Type) -> Type:
        """Every struct/enum name in `ty` resolved to its table entry."""
        from sushi_lang.semantics.type_resolution import resolve_type_recursively
        structs = self.monomorphizer.struct_table.by_name if self.monomorphizer.struct_table else {}
        enums = self.monomorphizer.enum_table.by_name if self.monomorphizer.enum_table else {}
        return resolve_type_recursively(ty, structs, enums)

    def _bind_destructure(self, stmt, var_types: Dict[str, Type]) -> None:
        """Each binder of a destructure is a local for the calls after it (#555's rule)."""
        from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements

        inferrer = self._get_arg_inferrer(var_types)
        value_type = inferrer.infer_expression_type(stmt.value) if inferrer else None

        def bind(targets, whole) -> None:
            elements: tuple = tuple_elements(whole) if is_tuple_type(whole) else ()
            if len(elements) != len(targets):
                elements = (None,) * len(targets)
            for target, element in zip(targets, elements, strict=True):
                if target.nested is not None:
                    bind(target.nested, element)
                elif target.name is not None and (target.ty or element) is not None:
                    var_types[target.name] = target.ty or element

        bind(stmt.targets, value_type)

    def _collect_from_expr(self, expr, var_types: Dict[str, Type]) -> None:
        """Recursively scan expression for generic function calls."""
        from sushi_lang.semantics.ast import (
            Call, Name, BinaryOp, UnaryOp, TryExpr, DotCall,
            IndexAccess, ArrayLiteral, EnumConstructor, CastExpr,
            InterpolatedString, Borrow, RangeExpr, Spread, MemberAccess,
            MethodCall, DynamicArrayFrom, DynamicArrayNew, BlankLit, Lambda,
            IntLit, FloatLit, StringLit, BoolLit, Block, TupleLiteral,
        )

        if isinstance(expr, Call):
            if isinstance(expr.callee, Name):
                function_name = expr.callee.id

                generic_func = self._generic_def(self._asking_unit, function_name)
                if generic_func is not None:
                    type_args = self._call_type_args(expr, generic_func, var_types)

                    if type_args:
                        # Track this instantiation for later processing
                        # We don't monomorphize recursively here to avoid registration issues
                        # Instead, we add to a worklist that will be processed by monomorphize_all_functions
                        cache_key = (getattr(generic_func, "unit_name", None),
                                     function_name, type_args)
                        self._record_site(function_name, type_args, expr.loc)
                        if cache_key not in self.monomorphizer.func_cache and hasattr(self.monomorphizer, 'pending_instantiations'):
                            self.monomorphizer.pending_instantiations.add(cache_key)

            # Recurse into arguments: a generic call nested inside another call's argument
            # (e.g. f(g(x))) was missed, the monomorphizer's own #191 (issue #214).
            for arg in getattr(expr, "args", []) or []:
                self._collect_from_expr(arg, var_types)

        elif isinstance(expr, BinaryOp):
            self._collect_from_expr(expr.left, var_types)
            self._collect_from_expr(expr.right, var_types)
        elif isinstance(expr, (UnaryOp, TryExpr, CastExpr, Borrow)):
            self._collect_from_expr(expr.expr, var_types)
        elif isinstance(expr, DotCall):
            self._collect_from_expr(expr.receiver, var_types)
            for arg in expr.args:
                self._collect_from_expr(arg, var_types)
        elif isinstance(expr, IndexAccess):
            self._collect_from_expr(expr.array, var_types)
            self._collect_from_expr(expr.index, var_types)
        elif isinstance(expr, ArrayLiteral):
            for element in expr.elements:
                self._collect_from_expr(element.value, var_types)
                if element.count is not None:
                    self._collect_from_expr(element.count, var_types)
        elif isinstance(expr, EnumConstructor):
            for arg in expr.args:
                self._collect_from_expr(arg, var_types)
        elif isinstance(expr, InterpolatedString):
            for part in expr.parts:
                if not isinstance(part, str):
                    self._collect_from_expr(part, var_types)
        elif isinstance(expr, RangeExpr):
            self._collect_from_expr(expr.start, var_types)
            self._collect_from_expr(expr.end, var_types)
        elif isinstance(expr, Spread):
            self._collect_from_expr(expr.value, var_types)
        elif isinstance(expr, MemberAccess):
            self._collect_from_expr(expr.receiver, var_types)
        elif isinstance(expr, MethodCall):
            self._collect_from_expr(expr.receiver, var_types)
            for arg in expr.args:
                self._collect_from_expr(arg, var_types)
        elif isinstance(expr, DynamicArrayFrom):
            self._collect_from_expr(expr.elements, var_types)
        elif isinstance(expr, TupleLiteral):
            for item in expr.elements:
                self._collect_from_expr(item, var_types)
        elif isinstance(expr, Lambda):
            # An expression-body lambda scans directly. A block-body lambda (a `let` RHS) is
            # walked in _collect_nested_instantiations, which has the generic_func needed to
            # rebuild its var-type scope.
            if not isinstance(expr.body, Block):
                self._collect_from_expr(expr.body, var_types)
        elif isinstance(expr, (IntLit, FloatLit, StringLit, BoolLit, Name,
                               BlankLit, DynamicArrayNew)):
            pass

    def _get_arg_inferrer(self, var_types: Dict[str, Type]):
        """The typecheck pass's inference over the whole program, seeded with this scope.

        One per call, so no call sees the scope of another; it writes no stamp (#806).
        """
        tables = getattr(self.monomorphizer, "tables", None)
        if tables is None:
            return None
        from sushi_lang.semantics.passes.types import ReadOnlyInferrer
        inferrer = ReadOnlyInferrer(tables, tables.namespaces.get(self._asking_unit))
        inferrer.variable_types = var_types
        return inferrer

    def _call_type_args(
        self,
        call: 'Call',
        generic_func: 'GenericFuncDef',
        var_types: Dict[str, Type]
    ) -> Optional[Tuple[Type, ...]]:
        """The type arguments of a generic call inside a monomorphized body: explicit, or solved.

        The arguments are typed by the typecheck pass's shared inferrer over the copy's
        locals and solved by the one solver, the same solve the instantiate and
        typecheck passes run (#795). This copy had no pack and no lambda path, so a
        variadic or lambda call inside a generic body was solved by the passes around
        it and never instantiated here.
        """
        from sushi_lang.semantics.ast import Name
        from sushi_lang.semantics.generics.explicit_type_args import (
            check_explicit_type_arg_arity, resolve_explicit_type_args)
        from sushi_lang.semantics.generics.pack_inference import (
            infer_call_arg_type, infer_flat_type_args)

        structs = self.monomorphizer.struct_table.by_name if self.monomorphizer.struct_table else {}
        enums = self.monomorphizer.enum_table.by_name if self.monomorphizer.enum_table else {}
        if call.type_args:
            if check_explicit_type_arg_arity(generic_func, len(call.type_args)) is not None:
                return None
            return resolve_explicit_type_args(call.type_args, structs, enums)

        from sushi_lang.semantics.passes.types.calls.generics import names_generic_fn_value
        inferrer = self._get_arg_inferrer(var_types)
        arg_types: list[Type | None] = []
        for arg_expr in getattr(call, "args", []) or []:
            # A generic function value is typed by the callee, as in the passes around
            # this one (#1029).
            if inferrer is not None and names_generic_fn_value(inferrer, arg_expr):
                arg_types.append(None)
                continue
            # The var-type map is the fallback for unit-test paths with no SymbolTables.
            arg_type = infer_call_arg_type(inferrer, arg_expr)
            if arg_type is None and isinstance(arg_expr, Name) and arg_expr.id in var_types:
                arg_type = var_types[arg_expr.id]
            if arg_type is None:
                return None
            arg_types.append(arg_type)

        return infer_flat_type_args(generic_func, arg_types, structs, enums)
