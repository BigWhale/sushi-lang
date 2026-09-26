"""Built-in extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64,
bool).
"""

from typing import Any, Callable, Optional
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType
import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.common import register_builtin_method, BuiltinMethod
from sushi_lang.sushi_stdlib.src import conversions, ir_common
from sushi_lang.backend.constants import INT8_BIT_WIDTH
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.diagnostics import InternalCompilerError


_TYPE_CONVERSION_SPECS = {
    BuiltinType.I8: ('integer', True, 8),
    BuiltinType.I16: ('integer', True, 16),
    BuiltinType.I32: ('integer', True, 32),
    BuiltinType.I64: ('integer', True, 64),
    BuiltinType.U8: ('integer', False, 8),
    BuiltinType.U16: ('integer', False, 16),
    BuiltinType.U32: ('integer', False, 32),
    BuiltinType.U64: ('integer', False, 64),
    BuiltinType.F32: ('float', False, 32),  # is_double=False
    BuiltinType.F64: ('float', True, 64),   # is_double=True
    BuiltinType.BOOL: ('bool', None, None),
    BuiltinType.STRING: ('string', None, None),
}


def _emit_generic_to_str(prim_type: BuiltinType) -> Any:
    """Create a to_str() emitter function for the given primitive type."""
    spec = _TYPE_CONVERSION_SPECS.get(prim_type)
    if not spec:
        raise_internal_error("CE0073", type=prim_type)

    kind, param1, param2 = spec

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Generic to_str() emitter created by factory."""
        if len(call.args) != 0:
            raise_internal_error("CE0078", got=len(call.args))

        if kind == 'integer':
            return codegen.runtime.formatting.emit_integer_to_string(receiver_value, is_signed=param1, bit_width=param2)
        elif kind == 'float':
            return codegen.runtime.formatting.emit_float_to_string(receiver_value, is_double=param1)
        elif kind == 'bool':
            return codegen.runtime.formatting.emit_bool_to_string(receiver_value)
        elif kind == 'string':
            return receiver_value
        else:
            raise_internal_error("CE0075", kind=kind)

    return emitter


primitive_types = [
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
]

emitters = {prim_type: _emit_generic_to_str(prim_type) for prim_type in primitive_types}

for prim_type in primitive_types:
    register_builtin_method(
        prim_type,
        BuiltinMethod(
            name="to_str",
            return_type=BuiltinType.STRING,
            description=f"Convert {prim_type} to string representation",
            llvm_emitter=emitters[prim_type],
        )
    )


def generate_module_ir() -> ir.Module:
    """Generate the `sushi_<T>_to_str` functions, one for each row of `_TYPE_CONVERSION_SPECS`."""
    module = ir_common.create_stdlib_module("core.primitives")
    string_type = get_string_type()

    for prim_type, (kind, flag, width) in _TYPE_CONVERSION_SPECS.items():
        param_type, emit_body = _to_str_parts(kind, flag, width, string_type)
        func = ir.Function(module, ir.FunctionType(string_type, [param_type]),
                           name=f"sushi_{prim_type}_to_str")
        builder = ir.IRBuilder(func.append_basic_block(name="entry"))
        builder.ret(emit_body(module, builder, func.args[0]))

    return module


_BodyEmitter = Callable[[ir.Module, ir.IRBuilder, ir.Value], ir.Value]


def _to_str_parts(kind: str, flag: Optional[bool], width: Optional[int],
                  string_type: ir.LiteralStructType) -> tuple[ir.Type, _BodyEmitter]:
    """The parameter type and the body emitter of one `to_str` function, from its kind."""
    if kind == 'integer' and width is not None:
        signed, bits = bool(flag), width
        return ir.IntType(bits), lambda m, b, v: conversions.emit_integer_to_string(m, b, v, signed, bits)
    if kind == 'float':
        is_double = bool(flag)
        param = ir.DoubleType() if is_double else ir.FloatType()
        return param, lambda m, b, v: conversions.emit_float_to_string(m, b, v, is_double)
    if kind == 'bool':
        return ir.IntType(INT8_BIT_WIDTH), conversions.emit_bool_to_string
    if kind == 'string':
        return string_type, lambda m, b, v: v
    raise InternalCompilerError("CE0075", kind=kind)
