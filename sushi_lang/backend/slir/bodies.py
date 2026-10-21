"""The SLIR of the program, as the backend reads it: the `SlirFunction` of a declaration.

`codegen_for` (`compiler/pipeline.py`) builds it from the analysis, and the module walk of
`LLVMCodegen` asks it for the body of each declaration that it defines. The SLIR program
keys a function by the `DeclId` of its SHIR body, and the SHIR program gives the body of a
declaration.
"""
from __future__ import annotations

from dataclasses import dataclass

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.shir.lowering import LoweredProgram
from sushi_lang.semantics.slir.function import SlirFunction, SlirProgram


@dataclass(frozen=True, slots=True)
class SlirBodies:
    """The SHIR program and the SLIR program of one analysis."""
    shir: LoweredProgram
    slir: SlirProgram

    def function_of(self, decl: object) -> SlirFunction:
        """The `SlirFunction` of the body of `decl`. A declaration whose body did not
        finish the SLIR lowering cannot reach the backend: that is an internal error."""
        body = self.shir.body_of(decl)
        outcome = None if body is None else self.slir.outcome_of(body.fn_id)
        if not isinstance(outcome, SlirFunction):
            raise_internal_error(
                "CE0000", detail=f"the body of '{getattr(decl, 'name', decl)}' reached the "
                                 f"backend with no SLIR function")
        return outcome
