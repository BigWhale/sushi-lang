"""Helper functions for LLVM function management."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, Param, ExtendDef
from sushi_lang.semantics.typesys import Type as Ty, BuiltinType, DynamicArrayType, EnumType
from sushi_lang.backend.ownership import relinquish
from sushi_lang.internals.errors import raise_internal_error, InternalCompilerError

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def callee_owns_param(param) -> bool:
    """Does the CALLEE own this parameter, and therefore free it at scope exit?"""
    from sushi_lang.semantics.param_modes import param_mode
    return param_mode(param).consumes


def declared_result_of(codegen: 'LLVMCodegen', fn: FuncDef) -> EnumType:
    """The Result a function returns: `Result@(T, E)` as written, else the one `T | E` implies."""
    from sushi_lang.semantics.generics.results import is_result_enum
    from sushi_lang.semantics.typesys import GenericTypeRef
    from sushi_lang.backend.generics.result_builder import implicit_result_of
    if isinstance(fn.ret, EnumType) and is_result_enum(fn.ret):
        return fn.ret
    result = None
    if not (isinstance(fn.ret, GenericTypeRef) and fn.ret.base_name == "Result"):
        result = implicit_result_of(codegen, fn)
    if result is None:
        raise InternalCompilerError(
            "CE0015", message=f"{fn.name}: no Result type for return type {fn.ret!r}")
    return result


class FunctionHelpers:
    """Utility functions for function emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize helpers with reference to main codegen instance."""
        self.codegen = codegen
        self._variable_types_stack: list[dict] = []

    def params_of(self, fn: FuncDef) -> List[Tuple[str, Ty]]:
        """Extract parameter information from function definition."""
        out: List[Tuple[str, Ty]] = []
        for idx, p in enumerate(getattr(fn, "params", ())):
            if not isinstance(p, Param):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] must be Param, got {type(p).__name__}")

            if not isinstance(p.name, str):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] name must be str, got {type(p.name).__name__}")

            if p.ty is None:
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has no type")

            out.append((p.name, p.ty))
        return out

    def get_extension_method_name(self, ext: ExtendDef) -> str:
        """Generate unique function name for extension method."""
        if ext.target_type and isinstance(ext.target_type, BuiltinType):
            target_type_name = ext.target_type.value
        else:
            target_type_name = str(ext.target_type) if ext.target_type else "unknown"

        from sushi_lang.semantics.generics.name_mangling import extension_symbol
        return extension_symbol(target_type_name, ext.name,
                                getattr(ext, "method_type_args", None) or ())

    def emit_default_return(self, fn: FuncDef) -> None:
        """Refuse a function body that left its last block open (#849).

        CE0107 refuses a body that can reach its end, and the `if` and `match` emitters
        leave no merge block that no arm branches to. So no program arrives here, and
        there is no implicit `Result.Ok(~)` or `Result.Err` to emit.
        """
        raise InternalCompilerError(
            "CE0015", message=f"{fn.name}: the body left its last block open with no return")

    def emit_default_return_for_extension(self, ret_type: Ty | None) -> None:
        """Emit default return value for extension method without explicit return."""
        if ret_type is None:
            return

        from sushi_lang.backend.statements import utils
        utils.emit_scope_cleanup(self.codegen)

        value_llvm_type = self.codegen.types.ll_type(ret_type)
        zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
        self.codegen.builder.ret(zero_value)

    def begin_function(self, llvm_fn: ir.Function,
                       fn_def: FuncDef | ExtendDef | None = None) -> None:
        """Initialize function emission context."""
        self.codegen.func = llvm_fn
        # The borrow pass stamps the names whose moves do not dominate their scope exit on the
        # BODY block (#414); registration arms a runtime drop flag for exactly those.
        body = getattr(fn_def, "body", None) if fn_def is not None else None
        self.codegen.current_conditional_moves = frozenset(
            getattr(body, "conditional_move_names", ()) or ())

        # `variable_types` is per-FUNCTION state. Per-module, an entry one function wrote
        # stayed readable by every later one -- wrong DATA for a value type, wrong CODE for
        # a `ReferenceType`, since `is_reference_parameter` keys on it. Save and restore
        # rather than clear: an out-of-line destructor body emitted mid-function nests.
        self._variable_types_stack.append(self.codegen.variable_types)
        self.codegen.variable_types = {}

        # Two blocks, and the entry one holds the branch and the stack slots alone. The
        # slots go there through `memory.entry_alloca`, which finds the block from the
        # builder rather than from a field the caller has to keep in step.
        entry = llvm_fn.append_basic_block(name="entry")
        start = llvm_fn.append_basic_block(name="start")

        self.codegen.builder = ir.IRBuilder(start)

        self.codegen.memory.reset_scope_stack()
        self.codegen.memory.push_scope()

        from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
        self.codegen.dynamic_arrays = DynamicArrayManager(self.codegen.builder, self.codegen)

        ir.IRBuilder(entry).branch(start)

        param_semantic_types = {}
        if fn_def is not None:
            for param in fn_def.params:
                if param.ty is not None:
                    param_semantic_types[param.name] = param.ty

        param_slots = []
        for i, arg in enumerate(llvm_fn.args):
            pname = arg.name or f"arg{i}"

            semantic_type = param_semantic_types.get(pname)

            # For reference parameters, the arg is already a pointer, so we store the pointer itself
            # rather than loading through it. This allows us to use the reference transparently.
            # When the parameter is used (in _emit_name), we'll load through this pointer.
            slot = self.codegen.memory.entry_alloca(arg.type, pname)
            self.codegen.memory.track_local(pname, slot, semantic_type)

            param_slots.append((arg, slot))

        # A `nom` parameter TAKES OWNERSHIP, so its owned bit must survive. Every other
        # mode is a BORROW: clearing the copy's owned bit means the body can never free the
        # caller's buffer (#145). Consuming a borrow is CE2411, so this guards reads only.
        owning_params: set[str] = set()
        if fn_def is not None:
            for param in fn_def.params:
                if callee_owns_param(param):
                    owning_params.add(param.name)

        for arg, slot in param_slots:
            val = arg
            if (self.codegen.types.is_string_type(arg.type)
                    and (arg.name or "") not in owning_params):
                val = self.codegen.builder.insert_value(arg, ir.Constant(self.codegen.i8, 0), 2)
            self.codegen.builder.store(val, slot)

        # One question, asked of the DECLARATION: `callee_owns_param`. Asking the
        # implementation instead is how one feature came to free its parameters in a `.slib`
        # build and not in a generated stdlib one.
        #
        # A native variadic `...T` array is the one parameter the CALLER synthesizes, so the
        # callee adopts it whatever the mode says.
        if fn_def is not None:
            slot_by_name = {arg.name or f"arg{i}": slot
                            for i, (arg, slot) in enumerate(param_slots)}
            for param in fn_def.params:
                slot = slot_by_name.get(param.name)
                if slot is None:
                    continue

                is_variadic = (getattr(param, "is_variadic", False)
                               and isinstance(param.ty, DynamicArrayType))
                if is_variadic or callee_owns_param(param):
                    self.codegen.memory.register_owning_value(param.name, param.ty, slot)
                    continue

                # Registered and immediately RELINQUISHED. The registration is what a
                # REBIND needs -- the value it puts there has no other owner and would leak.
                # The relinquish is what the CALLER needs -- the value that arrives is
                # theirs, so no exit path may free it. Two facts, one slot, in that order.
                self.codegen.memory.register_owning_value(param.name, param.ty, slot)
                relinquish(self.codegen, param.name)

    def end_function(self) -> None:
        """Clean up function emission context."""
        self.codegen.current_conditional_moves = frozenset()
        self.codegen.func = None
        self.codegen.builder = None
        self.codegen.memory.reset_scope_stack()
        self.codegen.variable_types = (
            self._variable_types_stack.pop() if self._variable_types_stack else {}
        )


def declare_stdlib_function(
    module: ir.Module,
    func_name: str,
    return_type: ir.Type,
    param_types: list[ir.Type]
) -> ir.Function:
    """Declare an external stdlib function."""
    if func_name in module.globals:
        existing = module.globals[func_name]
        if isinstance(existing, ir.Function):
            return existing

    fn_type = ir.FunctionType(return_type, param_types)
    func = ir.Function(module, fn_type, name=func_name)
    return func
