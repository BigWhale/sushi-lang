"""A function type is not an extension target, and the refusal is one diagnostic (#771).

The extension-target tuple (`generics/extension_targets.py:CONCRETE_EXTENSION_TARGETS`)
answers "may an extension name this target" and nothing else. A function type is not in
it, and the collect pass refuses a declaration that names one (CE2110) at the target.
The body is not checked against a `self` that has no type, and a call of the refused
method is not an undefined name.

A method call on a function value is still INFERRED: inference asks the method-family
table for every receiver, so `f.clone()` has the type of `f`.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
LIST is asserted here, on the analyze path.
"""
from __future__ import annotations



from sushi_lang.semantics.generics.extension_targets import CONCRETE_EXTENSION_TARGETS
from sushi_lang.semantics.typesys import FunctionType








def test_a_function_type_is_not_an_extension_target():
    assert not issubclass(FunctionType, CONCRETE_EXTENSION_TARGETS)






