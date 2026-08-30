"""Helper functions for LLVM function management."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, Param, ExtendDef
from sushi_lang.semantics.typesys import Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, UnknownType, ReferenceType, ForeignPtrType
from sushi_lang.backend import enum_utils
from sushi_lang.backend.ownership import relinquish
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def callee_owns_param(param) -> bool:
    """Does the CALLEE own this parameter, and therefore free it at scope exit?"""
    from sushi_lang.semantics.param_modes import param_mode
    return param_mode(param).consumes


class FunctionHelpers:
    """Utility functions for function emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize helpers with reference to main codegen instance."""
        self.codegen = codegen
        self._variable_types_stack: list[dict] = []

    def is_valid_param_type(self, param_type: Ty) -> bool:
        """Check if a type is valid for function parameters."""
        if param_type in (
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
        ):
            return True

        if isinstance(param_type, (ArrayType, DynamicArrayType)):
            return True

        if isinstance(param_type, StructType):
            return True

        if isinstance(param_type, EnumType):
            return True

        if isinstance(param_type, ReferenceType):
            return True

        if isinstance(param_type, ForeignPtrType):
            return True

        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(param_type, FunctionType):
            return True

        if isinstance(param_type, UnknownType):
            if hasattr(self.codegen, 'struct_table') and param_type.name in self.codegen.struct_table.by_name:
                return True
            if hasattr(self.codegen, 'enum_table') and param_type.name in self.codegen.enum_table.by_name:
                return True

        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(param_type, GenericTypeRef):
            return True

        return False

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

            if not self.is_valid_param_type(p.ty):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has invalid type {p.ty!r}")

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

    def emit_default_return(self, ret_type: Ty | None) -> None:
        """Emit default return value for function without explicit return."""
        if ret_type is None:
            return

        from sushi_lang.backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        from sushi_lang.backend.generics.result_builder import intern_result
        std_error = self.codegen.enum_table.by_name.get("StdError")
        result_type = intern_result(self.codegen, ret_type, std_error if std_error else ret_type)
        result_llvm_type = self.codegen.types.ll_type(result_type)

        result_enum_name = str(result_type)
        if result_enum_name in self.codegen.enum_table.by_name:
            result_enum = self.codegen.enum_table.by_name[result_enum_name]
            # Use enum constructor emission for Err()
            # Result.Err() has no arguments, variant index is 1 (Ok=0, Err=1)
            variant_index = result_enum.get_variant_index("Err")

            err_result = enum_utils.construct_enum_variant(
                self.codegen, result_llvm_type, variant_index=variant_index,
                data=None, name_prefix="Result_Err"
            )

            self.codegen.builder.ret(err_result)
        else:
            value_llvm_type = self.codegen.types.ll_type(ret_type)
            zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
            err_result = ir.Constant(result_llvm_type, [
                ir.Constant(self.codegen.i1, 0),  # is_ok = 0 (Err)
                zero_value                         # value = zero/default
            ])
            self.codegen.builder.ret(err_result)

    def emit_default_return_for_extension(self, ret_type: Ty | None) -> None:
        """Emit default return value for extension method without explicit return."""
        if ret_type is None:
            return

        from sushi_lang.backend.statements import utils
        utils.emit_scope_cleanup(self.codegen, cleanup_type='all')

        value_llvm_type = self.codegen.types.ll_type(ret_type)
        zero_value = self.codegen.utils.get_zero_value(value_llvm_type)
        self.codegen.builder.ret(zero_value)

    def begin_function(self, llvm_fn: ir.Function, fn_def: FuncDef | None = None) -> None:
        """Initialize function emission context."""
        self.codegen.func = llvm_fn
        self.codegen.entry_branch = None
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

        entry = llvm_fn.append_basic_block(name="entry")
        start = llvm_fn.append_basic_block(name="start")

        self.codegen.entry_block = entry
        self.codegen.builder = ir.IRBuilder(start)
        self.codegen.alloca_builder = ir.IRBuilder(entry)
        self.codegen.alloca_builder.position_at_start(entry)

        self.codegen.memory.reset_scope_stack()
        self.codegen.memory.push_scope()

        from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
        self.codegen.dynamic_arrays = DynamicArrayManager(self.codegen.builder, self.codegen)
        self.codegen.dynamic_arrays.push_scope()

        self.codegen.entry_branch = self.codegen.alloca_builder.branch(start)

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
            current_scope_level = self.codegen.memory._scope_depth
            self.codegen.memory._scope_vars[current_scope_level].add(pname)

            if pname not in self.codegen.memory._locals:
                self.codegen.memory._locals[pname] = []
            self.codegen.memory._locals[pname].append((current_scope_level, slot))

            if semantic_type is not None:
                if pname not in self.codegen.memory._types:
                    self.codegen.memory._types[pname] = []
                self.codegen.memory._types[pname].append((current_scope_level, semantic_type))

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
        self.codegen.alloca_builder = None
        self.codegen.entry_block = None
        self.codegen.memory.reset_scope_stack()
        self.codegen.entry_branch = None
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
