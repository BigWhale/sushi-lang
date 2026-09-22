"""What an expression YIELDS: the typecheck pass's inference visitor."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.extension_targets import (
    CONCRETE_EXTENSION_TARGETS)
from sushi_lang.semantics.name_ladder import BareName, classify

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.visitors import NodeVisitor
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType
from sushi_lang.semantics.type_predicates import is_string_convertible
from sushi_lang.semantics.ast import (
    Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom, CastExpr, EnumConstructor, TryExpr, RangeExpr, Borrow, Spread, Lambda,
    BlankLit, MemberAccess
)
from sushi_lang.semantics.passes.types.visit.helpers import (
    function_value_type_of, infer_lambda_type)


# Stdlib modules whose registry declares a return type outright. `math` is absent on
# purpose: its return type depends on the argument types, so it keeps its own branch.
_REGISTRY_TYPED_STDLIB_MODULES = ("time", "sys/env", "sys/process", "random", "io/files",
                                  "net/socket")


class _InferenceRungs:
    """Section 8's ladder as the typecheck pass answers it (`name_ladder.Rungs`).

    Every lookup here is UNIT-SCOPED, which is what makes this a second answerer rather
    than a duplicate of the scope pass's: a constant, a function and a generic function
    each resolve through the asking unit's view (section 13.1), and the scope pass has
    no unit view of the function table at all.
    """

    def __init__(self, type_validator: 'TypeValidator') -> None:
        self.tv = type_validator

    def is_local(self, name: str) -> bool:
        return self.tv.variable_types.get(name) is not None

    def is_constant(self, name: str) -> bool:
        return self.tv.const_sig(name) is not None

    def is_stdlib_constant(self, name: str) -> bool:
        from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
        return lookup_stdlib_constant(name, self.tv.scope) is not None

    def is_function(self, name: str) -> bool:
        return (self.tv.func_sig(name) is not None
                or self.tv.generic_sig(name) is not None)

    def is_namespace(self, name: str) -> bool:
        return self.tv.namespaces.is_namespace(name)

    def is_type(self, name: str) -> bool:
        from sushi_lang.semantics import statics
        return statics.names_a_type(
            name,
            structs=self.tv.struct_table.by_name.keys(),
            enums=self.tv.enum_table.by_name.keys(),
            generic_structs=self.tv.generic_struct_table.by_name.keys(),
            generic_enums=self.tv.generic_enum_table.by_name.keys())


class TypeInferenceVisitor(NodeVisitor[Optional[Type]]):
    """Visitor for type inference using the Visitor Pattern."""

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    def _materialize_wrapper(self, ty: Optional[Type]) -> Optional[Type]:
        """The interned wrapper a WRITTEN type names, or `ty` unchanged (#755).

        A field's declared type, a registry-declared stdlib return type and a substituted
        signature all reach this with the same spelling and want the same answer, so they
        read `utils.intern_declared_wrapper` and not three bodies of their own.
        """
        from ..utils import intern_declared_wrapper

        interned = intern_declared_wrapper(self.type_validator, ty)
        return interned if interned is not None else ty

    def visit_intlit(self, node: IntLit) -> Optional[Type]:
        """Infer integer literal type (context-typed if stamped, else default i32)."""
        return node.resolved_type or BuiltinType.I32

    def visit_floatlit(self, node: FloatLit) -> Optional[Type]:
        """Infer float literal type (context-typed if stamped, else default f64)."""
        return node.resolved_type or BuiltinType.F64

    def visit_boollit(self, node: BoolLit) -> Optional[Type]:
        """Infer boolean literal type."""
        return BuiltinType.BOOL

    def visit_blanklit(self, node: 'BlankLit') -> Optional[Type]:
        """Infer blank literal type."""
        return BuiltinType.BLANK

    def visit_spread(self, node: Spread) -> Optional[Type]:
        """A bloomed argument `arr...` infers as the whole array type of its source."""
        return self.type_validator.infer_expression_type(node.value)

    def visit_stringlit(self, node: StringLit) -> Optional[Type]:
        """Infer string literal type."""
        return BuiltinType.STRING

    def visit_interpolatedstring(self, node: InterpolatedString) -> Optional[Type]:
        """Infer interpolated string type and validate expression types."""
        for part in node.parts:
            if not isinstance(part, str):
                expr_type = self.type_validator.infer_expression_type(part)
                if expr_type and not is_string_convertible(expr_type):
                    er.emit(
                        self.type_validator.reporter,
                        er.ERR.CE2035,
                        part.loc,
                        type=display_type(expr_type)
                    )
        return BuiltinType.STRING

    def visit_arrayliteral(self, node: ArrayLiteral) -> Optional[Type]:
        """Infer array literal type."""
        return self.type_validator._infer_array_literal_type(node)

    def visit_indexaccess(self, node: IndexAccess) -> Optional[Type]:
        """Infer index access type."""
        return self.type_validator._infer_index_access_type(node)

    def visit_memberaccess(self, node: MemberAccess) -> Optional[Type]:
        """Infer member access type (a struct field, or a constant behind an alias)."""
        from sushi_lang.semantics.passes.types.calls.namespaced import (
            fold_namespaced_enum, infer_namespaced_member)

        # `geo.Sign.Plus` with no payload is a MemberAccess over a MemberAccess. The
        # fold leaves `Sign.Plus`, which the arms below and the back end already read.
        fold_namespaced_enum(self.type_validator, node)

        if self.type_validator.namespace_of(node.receiver) is not None:
            return infer_namespaced_member(self.type_validator, node)

        if isinstance(node.receiver, Name):
            variant_type = self._bare_variant_type(node)
            if variant_type is not None:
                return variant_type

        receiver_type = self.type_validator.infer_expression_type(node.receiver)

        if receiver_type is None:
            return None

        if isinstance(receiver_type, StructType):
            for field_name, field_type in receiver_type.fields:
                if field_name == node.member:
                    # Resolve generic types to semantic types where applicable
                    # E.g., GenericTypeRef("Result", [T, E]) → EnumType("Result<T, E>")
                    # This ensures pattern matching and other operations work correctly
                    resolved_type = self._materialize_wrapper(field_type)
                    return resolved_type

        return None

    def _bare_variant_type(self, node: MemberAccess) -> Optional[Type]:
        """The enum a bare `Enum.Variant` constructs, or None when the receiver is a value.

        The two answers `visit_dotcall` gives the parenthesized spelling: a plain enum
        is its table entry, and a generic one is whatever propagation stamped (#545).
        """
        tv = self.type_validator
        receiver_name = node.receiver.id
        if receiver_name in tv.variable_types:
            return None
        if node.resolved_enum_type is not None:
            return node.resolved_enum_type
        return tv.enum_table.by_name.get(receiver_name)

    def visit_name(self, node: Name) -> Optional[Type]:
        """Infer a bare name's type: section 8's ladder, from the one place it lives.

        The order used to be written out a second time here, and it drifted from the
        scope pass's at the type rung (#600). `name_ladder` holds the order now; the
        lookups stay this pass's, because they are unit-scoped and the scope pass's are
        not.
        """
        tv = self.type_validator
        rung = classify(node.id, _InferenceRungs(tv))

        if rung is BareName.LOCAL:
            from sushi_lang.semantics.typesys import ReferenceType
            var_type = tv.variable_types[node.id]
            if isinstance(var_type, ReferenceType):
                return var_type.referenced_type
            return var_type

        if rung is BareName.CONSTANT:
            # The record keeps the DECLARED spelling, which is an `UnknownType` for a
            # struct until the declaring unit's own typecheck resolves it. A reader in
            # another unit -- the instantiate pass inferring `stdin.share()??` as a generic
            # argument -- needs the interned type, so resolve it here, the one place a
            # bare constant or unit variable is typed.
            from sushi_lang.semantics.type_resolution import resolve_unknown_type
            const_sig = tv.const_sig(node.id)
            if const_sig is None:
                return None
            return resolve_unknown_type(const_sig.const_type,
                                        tv.struct_table.by_name,
                                        tv.enum_table.by_name)

        if rung is BareName.STDLIB_CONSTANT:
            # Below every declaration of this program and above a function value, at the
            # rung the back end reads it (#560). It used to be asked FIRST, so
            # `let i32 PI = 3` typed as an f64.
            from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
            stdlib_const = lookup_stdlib_constant(node.id, tv.scope)
            if stdlib_const is None:
                return None
            return self._materialize_wrapper(stdlib_const.get_return_type())

        if rung is BareName.FUNCTION:
            fn_value_type = function_value_type_of(tv, node.id)
            if fn_value_type is not None:
                return fn_value_type
            # A generic-fn reference with an explicit expected fn type (T2.3): solve the
            # type args and return the concrete FunctionType (the node is rewritten to
            # the mangled name during validation).
            if tv.generic_sig(node.id) is not None:
                from sushi_lang.semantics.passes.types.calls.generics import resolve_generic_fn_reference
                resolved = resolve_generic_fn_reference(
                    tv, node.id, getattr(node, "expected_type", None))
                if resolved is not None:
                    return resolved[1]
            return None

        # A NAMESPACE and a TYPE are names with no value, and NOTHING is undeclared. The
        # scope pass has already said so -- CE2105 for a type, CE1001 for nothing -- so
        # there is no type to infer and nothing to add here.
        return None

    def visit_lambda(self, node: Lambda) -> Optional[Type]:
        """Infer a lambda literal's type (its FunctionType)."""
        return infer_lambda_type(self.type_validator, node)

    def visit_unaryop(self, node: UnaryOp) -> Optional[Type]:
        """Infer unary operation type."""
        if node.op == "not":
            return BuiltinType.BOOL
        if node.op == "~":
            return self.type_validator.infer_expression_type(node.expr)
        if node.op == "neg":
            return self.type_validator.infer_expression_type(node.expr)
        return self.type_validator.infer_expression_type(node.expr)

    def visit_binaryop(self, node: BinaryOp) -> Optional[Type]:
        """Infer binary operation type directly (no delegation)."""
        if node.op in ["==", "!=", "<", "<=", ">", ">="]:
            return BuiltinType.BOOL

        if node.op in ["and", "or", "xor"]:
            return BuiltinType.BOOL

        if node.op in ["+", "-", "*", "/", "%"]:
            left_type = self.type_validator.infer_expression_type(node.left)
            right_type = self.type_validator.infer_expression_type(node.right)

            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                return None

            # The result is the common operand type. Mixed numerics are CE2510 from the
            # ExpressionValidator; None here avoids cascading mismatches.
            numeric = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                       BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                       BuiltinType.F32, BuiltinType.F64)
            if left_type == right_type and left_type in numeric:
                return left_type
            if left_type is None and right_type in numeric:
                return right_type
            if right_type is None and left_type in numeric:
                return left_type
            return None

        if node.op in ["&", "|", "^", "<<", ">>"]:
            return self.type_validator.infer_expression_type(node.left)

        return None

    def _intern_result(self, ok_type: Type, err_type: Type) -> Optional[Type]:
        """The interned ``Result<ok, err>`` EnumType -- what a call's return type IS at runtime.
        """
        from sushi_lang.semantics.generics.results import ensure_result_type_in_table
        return ensure_result_type_in_table(
            self.type_validator.enum_table, ok_type, err_type,
            struct_table=self.type_validator.struct_table.by_name,
        )


    def visit_call(self, node: Call) -> Optional[Type]:
        """Infer a function call's type and stamp the node with it.

        A method call and a `??` have carried the stamp since #379; a plain call
        carried none, so every backend reader of `stamped_semantic_type` fell through
        to reconstruction. A bool-returning stdlib call then printed 1 instead of
        true, in a hole and under a `not` alike (#523).
        """
        inferred = self._call_return_type(node)
        if inferred is not None:
            node.inferred_return_type = inferred
        return inferred

    def _call_return_type(self, node: Call) -> Optional[Type]:
        """What a call to this callee yields, before the stamp is parked on the node."""
        from sushi_lang.semantics.typesys import FunctionType
        # Call-through any expression yielding a function value (`env.f(x)`,
        # `obj.handler()`, `arr[0]()`). Yields Result<ok, err> like a direct call.
        if not isinstance(node.callee, Name):
            callee_ty = self.type_validator.infer_expression_type(node.callee)
            if isinstance(callee_ty, FunctionType):
                return self._intern_result(callee_ty.ok_type, callee_ty.err_type)
            return None

        function_name = node.callee.id

        callee_var_ty = self.type_validator.variable_types.get(function_name)
        if isinstance(callee_var_ty, FunctionType):
            return self._intern_result(callee_var_ty.ok_type, callee_var_ty.err_type)

        if function_name in self.type_validator.struct_table.by_name:
            return self.type_validator.struct_table.by_name[function_name]

        # A declaration answers before a name a flat `use` brought in, exactly as the
        # validating half decides it (section 8's ladder). Reading the standard library
        # first gave this unit's own `sin` the library's return type.
        func_sig = self.type_validator.func_sig(function_name)
        if func_sig is not None:
            return self.result_type_of(func_sig)

        # A GENERIC callee has no concrete signature yet: its instance is named by the
        # mangled symbol the validating half computes, and inference runs before that --
        # while a generic call sits in another generic call's ARGUMENT. The substituted
        # signature answers, and the Result it names is resolved by LOOKUP, never
        # interned early (#556).
        generic_func = self.type_validator.generic_sig(function_name)
        if generic_func is not None:
            from sushi_lang.semantics.passes.types.calls.generics import (
                generic_call_result_type)
            substituted = generic_call_result_type(self.type_validator, node, generic_func)
            if substituted is not None:
                return self._materialize_wrapper(substituted)

        # The registry is the single source of truth the backend reads too, so reading it
        # here keeps the two from drifting. The hardcoded copies this replaced had gone
        # stale: they looked up a one-arg "Result<i32>" that is never registered.
        #
        # math keeps its own branch below -- its return type depends on the arguments.
        for module_path in _REGISTRY_TYPED_STDLIB_MODULES:
            if not self.type_validator.scope.holds_module(module_path):
                continue
            stdlib_func = self.type_validator.func_table.lookup_stdlib_function(
                module_path, function_name
            )
            if stdlib_func is not None and not stdlib_func.is_constant:
                return self._materialize_wrapper(stdlib_func.get_return_type())

        if function_name in {'abs', 'min', 'max', 'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
            from sushi_lang.sushi_stdlib.src import math as math_module
            if math_module.is_builtin_math_function(function_name):
                param_types = []
                for arg in node.args:
                    arg_type = self.type_validator.infer_expression_type(arg)
                    if arg_type is not None:
                        param_types.append(arg_type)

                if function_name in {'abs', 'min', 'max'} and param_types:
                    return param_types[0]

                if function_name in {'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
                    return BuiltinType.F64

            return None

        return None

    def result_type_of(self, func_sig) -> Optional[Type]:
        """What a call to this signature yields: its return type, wrapped in Result.

        One derivation, so a call written through a namespace agrees with the bare
        form. Every arm below is the same rule read from a different spelling of the
        declared return.
        """
        if func_sig.ret_type is None:
            return None

        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        from sushi_lang.semantics.generics.results import signature_result_arms

        arms = signature_result_arms(
            func_sig.ret_type, func_sig.err_type,
            self.type_validator.enum_table.by_name.get("StdError"))
        if arms is not None:
            return self._intern_result(*arms)
        # Already the interned enum (the signature was resolved in place): wrapping it
        # again would produce Result<Result<T, E>, StdError>. A `Result` reference of
        # the wrong arity resolves by name, or stays what it is.
        if isinstance(func_sig.ret_type, GenericTypeRef):
            return resolve_unknown_type(
                func_sig.ret_type,
                self.type_validator.struct_table.by_name,
                self.type_validator.enum_table.by_name
            )
        return func_sig.ret_type

    def visit_methodcall(self, node: MethodCall) -> Optional[Type]:
        """Infer method call type and annotate node with inferred return type."""
        if (isinstance(node.receiver, Name)
                and node.receiver.id not in self.type_validator.variable_types):
            enum_name = node.receiver.id
            if enum_name in self.type_validator.enum_table.by_name:
                inferred_type = self.type_validator.enum_table.by_name[enum_name]
                node.inferred_return_type = inferred_type
                return inferred_type
            elif enum_name in self.type_validator.generic_enum_table.by_name:
                # This is a generic enum constructor (like Result.Ok())
                # We can't infer the complete type without more context
                # For now, return None and let the type be inferred from context
                return None

        receiver_type = self.type_validator.infer_expression_type(node.receiver)
        from sushi_lang.semantics.typesys import ReferenceType

        actual_type = receiver_type
        if isinstance(receiver_type, ReferenceType):
            actual_type = receiver_type.referenced_type

        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(actual_type, GenericTypeRef):
            type_args_str = ", ".join(str(arg) for arg in actual_type.type_args)
            type_name = f"{actual_type.base_name}<{type_args_str}>"
            if type_name in self.type_validator.struct_table.by_name:
                actual_type = self.type_validator.struct_table.by_name[type_name]
            elif type_name in self.type_validator.enum_table.by_name:
                actual_type = self.type_validator.enum_table.by_name[type_name]

        if isinstance(actual_type, CONCRETE_EXTENSION_TARGETS):
            from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
            inferred_type = METHOD_TYPE_REGISTRY.infer_method_type(
                actual_type, node.method, self.type_validator
            )

            # An extension or perk method's return type is a DECLARED spelling, so it
            # resolves before it is stamped: a `Maybe@(string)` left as a GenericTypeRef is
            # no EnumType, so every consumer reading the stamp fell through -- the backend
            # to a layout heuristic that answered `Maybe<i32>` and then mis-typed the
            # payload (#387).
            #
            # ONE ladder, shared with the validation half and with the `foreach` protocol
            # (`resolve_method`). Inference stays silent -- report=False -- because the
            # validation half owns CE2063. A `T[]` template and a method-generic template
            # both answer at the CALL SITE, so inference may be the first call site that
            # instantiates one.
            if inferred_type is None:
                from sushi_lang.semantics.passes.types.calls.methods import (
                    RESOLUTION_REPORTED, extension_call_result_type, resolve_method)
                resolved = resolve_method(self.type_validator, actual_type, node.method,
                                          call=node, report=False)
                if resolved is not None and resolved is not RESOLUTION_REPORTED:
                    # A channel method (`| E`, ruling R1) yields its interned Result at
                    # every call site -- one reader for what a method call yields, and
                    # the same one whether a perk or an extension answered. A perk method
                    # spells its bare return `ret` and an ExtensionMethod spells it
                    # `ret_type`, which is why the reader takes the method and not a type.
                    inferred_type = extension_call_result_type(
                        self.type_validator, resolved.method)

            if inferred_type is not None:
                node.inferred_return_type = inferred_type
                return inferred_type

        return None

    def visit_dotcall(self, node: DotCall) -> Optional[Type]:
        """Infer dot-call type and annotate node with inferred return type."""
        from sushi_lang.semantics.passes.types.calls.dotcall import (
            DotCallKind, copy_callee_stamps, resolve_dotcall)

        target = resolve_dotcall(self.type_validator, node, report=False)

        if target.kind is DotCallKind.FN_FIELD:
            node.inferred_return_type = self._intern_result(target.fn_type.ok_type,
                                                            target.fn_type.err_type)
            return node.inferred_return_type

        if target.kind is not DotCallKind.METHOD:
            return target.type

        inferred_type = self.visit_methodcall(target.method_call)
        if inferred_type is not None:
            node.inferred_return_type = inferred_type
        copy_callee_stamps(node, target.method_call)
        return inferred_type

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> Optional[Type]:
        """new() constructor requires context for type inference."""
        return None

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> Optional[Type]:
        """from(array_literal) can infer type from array literal elements."""
        return self.type_validator._infer_dynamic_array_from_type(node)

    def visit_castexpr(self, node: CastExpr) -> Optional[Type]:
        """Cast expression - return the target type."""
        return node.target_type

    def visit_enumconstructor(self, node: EnumConstructor) -> Optional[Type]:
        """EnumConstructor - return the enum type (including Result.Ok/Result.Err)."""
        if hasattr(node, 'resolved_enum_type') and node.resolved_enum_type is not None:
            return node.resolved_enum_type

        if node.enum_name in self.type_validator.enum_table.by_name:
            return self.type_validator.enum_table.by_name[node.enum_name]

        return None

    def visit_tryexpr(self, node: TryExpr) -> Optional[Type]:
        """Try expression (?? operator) - unwrap result-like enum to Ok type."""
        inner_type = self.type_validator.infer_expression_type(node.expr)

        if inner_type is None:
            return None

        # A first-class function value call yields a Result enum (not a concrete Result
        # EnumType); `??` unwraps it to its ok_type -- e.g. a captured closure called in
        # a lambda body, `f(x)??`.
        from sushi_lang.semantics.typesys import EnumType
        if isinstance(inner_type, EnumType):
            for variant_name in ("Ok", "Some"):
                variant = inner_type.get_variant(variant_name)
                if variant and variant.associated_types:
                    return variant.associated_types[0]

        return None

    def visit_rangeexpr(self, node: RangeExpr) -> Optional[Type]:
        """Infer type of range expression - always Iterator<i32>."""
        from sushi_lang.semantics.passes.types.inference import infer_range_expression_type
        return infer_range_expression_type(self.type_validator, node)

    def visit_borrow(self, node: Borrow) -> Optional[Type]:
        """Infer type of borrow expression (peek expr or poke expr)."""
        from sushi_lang.semantics.typesys import ReferenceType, BorrowMode

        inner_type = self.type_validator.infer_expression_type(node.expr)
        if inner_type is None:
            return None

        ref = getattr(node.expr, "namespace_ref", None)
        if ref is not None and ref.kind == "constant":
            # `poke geo.SIZE`: the alias reaches the record, and the record decides. The
            # spelling changes nothing -- a write to a constant is CE2400 here too, and
            # a `peek` reads it as it reads the bare name (#713).
            from sushi_lang.semantics.constant_borrow import (
                reject_borrow_of_constant)
            sig = self.type_validator.const_table.lookup(ref.name, ref.origin)
            if reject_borrow_of_constant(self.type_validator.err, ref.name, sig,
                                         node.expr.loc, mode=node.mutability):
                return None

        mutability = BorrowMode.PEEK if node.mutability == "peek" else BorrowMode.POKE
        return ReferenceType(referenced_type=inner_type, mutability=mutability)

    def generic_visit(self, node) -> Optional[Type]:
        """Default behavior for unknown nodes."""
        return None
