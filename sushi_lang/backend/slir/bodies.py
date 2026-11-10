"""The SLIR of the program, as the backend reads it: the `SlirFunction` of a declaration.

`codegen_for` (`compiler/pipeline.py`) builds it from the analysis, and the module walk of
`LLVMCodegen` asks it for the body of each declaration that it defines. The SLIR program
keys a function by the `DeclId` of its SHIR body, and the record of the declaration in the
declaration table holds that `DeclId` (docs/design/ir.md 6.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.slir.function import SlirFunction, SlirProgram

if TYPE_CHECKING:
    from sushi_lang.semantics.decl_table import FunctionDecl, MethodDecl


@dataclass(frozen=True, slots=True)
class SlirBodies:
    """The SLIR program of one analysis."""
    slir: SlirProgram

    def function_of(self, decl: FunctionDecl | MethodDecl) -> SlirFunction:
        """The `SlirFunction` of the body of `decl`. A declaration whose body did not
        finish the SLIR lowering cannot reach the backend: that is an internal error."""
        outcome = None if decl.body is None else self.slir.outcome_of(decl.body)
        if outcome is None:
            raise_internal_error(
                "CE0000", detail=f"the body of '{decl.name}' reached the backend with no "
                                 f"SLIR function")
        return outcome
