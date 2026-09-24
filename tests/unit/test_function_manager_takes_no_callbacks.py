"""The function manager's parts reach each other through `codegen`, never through a parameter.

Each part holds `codegen`, so a collaborator's method handed in as an argument is plumbing
that can go out of step with the one `codegen.functions` holds. Every parameter carries a
type annotation, and the C `main` has one emitter for both argument lists.
"""
from __future__ import annotations

import inspect

import pytest

from sushi_lang.backend.functions import LLVMFunctionManager
from sushi_lang.backend.functions.declarations import FunctionDeclarations
from sushi_lang.backend.functions.definitions import FunctionDefinitions
from sushi_lang.backend.functions.helpers import FunctionHelpers
from sushi_lang.backend.functions.main_wrapper import MainFunctionWrapper

PARTS = (LLVMFunctionManager, FunctionDeclarations, FunctionDefinitions,
         FunctionHelpers, MainFunctionWrapper)
COLLABORATOR_NAMES = {"helpers", "main_wrapper", "declarations", "definitions"}


def _methods():
    for cls in PARTS:
        for name, member in vars(cls).items():
            if inspect.isfunction(member) and name != "__init__":
                yield f"{cls.__name__}.{name}", member


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
