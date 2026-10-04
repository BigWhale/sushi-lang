"""A call of a built-in method that has a method-level type parameter (design 8.3).

The typecheck half of `semantics/generics/builtin_signatures.py`. Inference reads
`builtin_call_return_type`, and the validation half (`calls/methods.py`) reads
`solve_builtin_call` and reports what the solver leaves.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import MethodCall
    from sushi_lang.semantics.generics.builtin_signatures import (
        BuiltinInstance, BuiltinSignature)
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.typesys import Type


def solve_builtin_call(validator: 'TypeValidator', signature: 'BuiltinSignature',
                       receiver_type: 'Type', call: 'MethodCall'
                       ) -> tuple[Optional['BuiltinInstance'], tuple[str, ...]]:
    """The instance of `signature` that this call names, and the names left unsolved."""
    from sushi_lang.semantics.generics.builtin_signatures import solve_builtin_signature
    from sushi_lang.semantics.generics.pack_inference import infer_call_arg_type
    arg_types = [infer_call_arg_type(validator, arg) for arg in call.args]
    return solve_builtin_signature(signature, receiver_type, arg_types,
                                   validator.struct_table.by_name,
                                   validator.enum_table.by_name)


def instance_return_type(validator: 'TypeValidator',
                         instance: 'BuiltinInstance') -> Optional['Type']:
    """The return type of the instance, interned through the wrapper seam."""
    from sushi_lang.semantics.passes.types.utils import (
        intern_declared_wrapper, resolve_declared_type)
    interned = intern_declared_wrapper(validator, instance.ret_type)
    if interned is not None:
        return interned
    return resolve_declared_type(validator, instance.ret_type)


def builtin_call_return_type(validator: 'TypeValidator', signature: 'BuiltinSignature',
                             receiver_type: 'Type', call: 'MethodCall') -> Optional['Type']:
    """What the call yields, or None. Silent: the validation half reports every fault.

    A call that E3 refuses yields None, as a refused method-generic extension does, so
    no `Result` with a plain `E` is interned.
    """
    from sushi_lang.semantics.generics.builtin_signatures import refuses_an_error_argument
    instance, _unsolved = solve_builtin_call(validator, signature, receiver_type, call)
    if instance is None or refuses_an_error_argument(
            instance, validator.struct_table.by_name, validator.enum_table.by_name):
        return None
    return instance_return_type(validator, instance)
