"""Built-in extension methods for primitive types (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64,
bool).
"""

from typing import Any, Sequence
from sushi_lang.semantics.typesys import BuiltinType
import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.common import register_builtin_method, BuiltinMethod
from sushi_lang.internals.errors import raise_internal_error


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

    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
               receiver_type: ir.Type) -> ir.Value:
        """Generic to_str() emitter created by factory."""
        if args:
            raise_internal_error("CE0078", got=len(args))

        if kind == 'integer':
            return codegen.runtime.formatting.emit_integer_to_string(receiver_value, is_signed=param1, bit_width=param2)
        elif kind == 'float':
            return codegen.runtime.formatting.emit_float_to_string(receiver_value, is_double=param1)
        elif kind == 'bool':
            return codegen.runtime.formatting.emit_bool_to_string(receiver_value)
        elif kind == 'string':
            from sushi_lang.backend.ownership import copy_out
            return copy_out(codegen, receiver_value, BuiltinType.STRING)
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

