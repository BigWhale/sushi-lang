"""One answer to "does the compiler already define this method on this type?"."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.typesys import ReferenceType, Type


def builtin_method_exists(receiver_type: Type | None, method_name: str,
                          derived_methods: DerivedMethodTable) -> bool:
    """Is `method_name` a compiler-defined method on `receiver_type`?

    The family table answers (#812): a name is built in when a family of
    `METHOD_TYPE_REGISTRY` defines it on this receiver. A perk implementation does not
    change the answer -- it is the sanctioned override, and an extension of a built-in
    name is still CE2097. `derived_methods` is the COMPILATION's auto-derived pair (hash,
    clone) -- one program's `Point` is not another's, however alike the two names look
    (#601).
    """
    if receiver_type is None:
        return False
    if isinstance(receiver_type, ReferenceType):
        receiver_type = receiver_type.referenced_type
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
    return METHOD_TYPE_REGISTRY.answers(receiver_type, method_name, derived_methods)


def reject_builtin_miscount(reporter: Any, call: Any, callee: str,
                            arity: Mapping[str, int]) -> bool:
    """CE2009: a built-in method or static took the wrong number of arguments.

    `arity` is the family's table (`MethodFamily.arity`); a name it does not hold is
    measured elsewhere. `callee` is the name the text shows. Answers whether it reported.
    """
    expected = arity.get(call.method)
    if expected is None or len(call.args) == expected:
        return False
    er.emit(reporter, er.ERR.CE2009, call.loc,
            name=callee, expected=expected, got=len(call.args))
    return True


def derived_method(target_type: Type, *, name: str, kind: str, return_type: Type,
                   factory_getter: Callable[[str], Any], ice: Any) -> Any:
    """The auto-derived zero-argument method (`hash`, `clone`) of a type, to register.

    The emitter is LAZY: the backend deposits its factory for `kind` at import time, and
    semantics may not import the backend, so the factory is found at the first emission.
    A missing factory is the internal error `ice`. The count is the derived family's row
    (`MethodFamily.arity`), so the method carries no validator.
    """
    def emit(codegen, call, receiver_value, receiver_type, to_i1):
        factory = factory_getter(kind)
        if factory is None:
            raise_internal_error(ice.code, kind=kind)
        return factory(target_type)(codegen, call, receiver_value, receiver_type, to_i1)

    from sushi_lang.sushi_stdlib.src.common import BuiltinMethod
    return BuiltinMethod(
        name=name,
        return_type=return_type,
        description=f"Auto-derived {name} for {kind} {target_type}",
        llvm_emitter=emit,
    )
