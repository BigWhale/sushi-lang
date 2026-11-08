"""A call of a built-in method that has a method-level type parameter (design 8.3).

The typecheck half of `semantics/generics/builtin_signatures.py`. Inference reads
`builtin_call_return_type`, and the two validation halves (`calls/methods.py` over the AST,
`visit/signature_methods.py` over SHIR) read `settle_builtin_instance`, which reports what
the solver leaves.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Sequence

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.generics.builtin_signatures import (
        BuiltinInstance, BuiltinSignature)
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.typesys import Type


def settle_builtin_instance(validator: 'TypeValidator', signature: 'BuiltinSignature',
                            receiver_type: 'Type', arg_types: Sequence[Optional['Type']],
                            loc: Optional['Span'], method: str,
                            unsolved_help: Callable[[int], str]
                            ) -> Optional['BuiltinInstance']:
    """The instance that a call with these argument types names, or None after the fault
    is reported.

    A type parameter that the arguments leave unsolved is CE2063, with the help that
    `unsolved_help` gives for the count of names. A solved one in an `E` position that is
    not an error type is refused by E3.
    """
    from sushi_lang.semantics.error_types import reject_non_error_type
    from sushi_lang.semantics.generics.builtin_signatures import solve_builtin_signature

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    instance, unsolved = solve_builtin_signature(signature, receiver_type, arg_types,
                                                 structs, enums)
    if instance is None:
        if unsolved:
            names = ", ".join(f"'{n}'" for n in unsolved)
            er.emit_with(validator.reporter, er.ERR.CE2063, loc,
                         plural="s" if len(unsolved) > 1 else "",
                         names=names, method=method) \
                .help(unsolved_help(len(unsolved))).emit()
        return None
    refused = [name for name in signature.error_parameters
               if reject_non_error_type(validator.reporter, instance.substitution[name],
                                        loc, structs, enums)]
    return None if refused else instance


def instance_return_type(validator: 'TypeValidator',
                         instance: 'BuiltinInstance') -> Optional['Type']:
    """The return type of the instance, interned through the wrapper seam."""
    from sushi_lang.semantics.passes.types.utils import (
        intern_declared_wrapper, resolve_declared_type)
    interned = intern_declared_wrapper(validator, instance.ret_type)
    if interned is not None:
        return interned
    return resolve_declared_type(validator, instance.ret_type)


