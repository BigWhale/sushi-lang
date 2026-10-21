"""The function manager's parts reach each other through `codegen`, never through a parameter.

Each part holds `codegen`, so a collaborator's method handed in as an argument is plumbing
that can go out of step with the one `codegen.functions` holds. Every parameter carries a
type annotation, and the C `main` has one emitter for both argument lists.

The SLIR function emitter (`backend/slir/`) is free functions, not a part of the manager:
the backend rule makes stateless translation free functions that take `codegen` first, so
it joins the parameter rule and not `PARTS`.
"""
from __future__ import annotations

import inspect

import pytest

from sushi_lang.backend.functions import LLVMFunctionManager
from sushi_lang.backend.functions.declarations import FunctionDeclarations
from sushi_lang.backend.functions.definitions import FunctionDefinitions
from sushi_lang.backend.functions.helpers import FunctionHelpers
from sushi_lang.backend.functions.main_wrapper import MainFunctionWrapper
from sushi_lang.backend.slir import bodies as slir_bodies
from sushi_lang.backend.slir import emit as slir_emit
from sushi_lang.backend.slir import intrinsics as slir_intrinsics
from sushi_lang.backend.slir import state as slir_state
from sushi_lang.backend.slir import values as slir_values

PARTS = (LLVMFunctionManager, FunctionDeclarations, FunctionDefinitions,
         FunctionHelpers, MainFunctionWrapper)
SLIR_EMITTER = (slir_bodies, slir_emit, slir_intrinsics, slir_state, slir_values)
COLLABORATOR_NAMES = {"helpers", "main_wrapper", "declarations", "definitions"}


def _methods():
    for cls in PARTS:
        for name, member in vars(cls).items():
            if inspect.isfunction(member) and name != "__init__":
                yield f"{cls.__name__}.{name}", member
    for module in SLIR_EMITTER:
        for name, member in vars(module).items():
            if inspect.isfunction(member) and member.__module__ == module.__name__:
                yield f"{module.__name__}.{name}", member
        for name, member in vars(module).items():
            if inspect.isclass(member) and member.__module__ == module.__name__:
                for attr, method in vars(member).items():
                    if inspect.isfunction(method) and not attr.startswith("__"):
                        yield f"{module.__name__}.{name}.{attr}", method


@pytest.mark.parametrize("qualname,method", list(_methods()), ids=lambda v: v if isinstance(v, str) else "")
def test_no_parameter_is_a_collaborator(qualname, method):
    for pname, param in inspect.signature(method).parameters.items():
        if pname in ("self", "llvm_fn"):
            continue
        assert not pname.endswith("_fn") and pname not in COLLABORATOR_NAMES, (
            f"{qualname} takes {pname!r}: read the collaborator from self.codegen.functions")
        assert param.annotation is not inspect.Parameter.empty, (
            f"{qualname} parameter {pname!r} has no type annotation")


def test_the_result_extractor_is_public():
    assert hasattr(LLVMFunctionManager, "extract_value_from_result_enum")
    assert not hasattr(LLVMFunctionManager, "_extract_value_from_result_enum")


def test_main_has_one_emitter():
    assert hasattr(MainFunctionWrapper, "emit_main")
    for retired in ("emit_main_with_args", "emit_main_without_args"):
        assert not hasattr(MainFunctionWrapper, retired), f"{retired} is back"
