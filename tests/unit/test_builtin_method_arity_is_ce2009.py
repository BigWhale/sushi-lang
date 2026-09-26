"""A built-in method called with the wrong number of arguments is CE2009, like every callee.

One fault answered four codes in three text shapes: CE2053 for a `List@(T)`, CE2016 for
a `HashMap`, an `Own` and a `Maybe`, CE2502 for `Result.realise()`, and CE2009 for an
array, a string, a derived method and a function (#799). The count of a built-in family
is one table on the family (`MethodFamily.arity`, `passes/types/method_registry.py`), read
before the family's own check.

`EXPECT_ERROR_CODE` is a substring test and cannot count, so the code SET is asserted
here, on the analyze path: one miscount answers exactly one CE2009.
"""
from __future__ import annotations




#: One miscount per family, and the callee the text names.




def test_the_retired_codes_are_not_registered():
    from sushi_lang.internals.errors import REGISTRY
    assert not {"CE2016", "CE2053", "CE2502"} & set(REGISTRY)
