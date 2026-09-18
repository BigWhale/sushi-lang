"""A perk-implementation method declares no type parameters of its own (#704).

The contract has no slot for one, so nothing could ever read the list. The grammar
admits it in that position for the reason it admits `static` and `public` there: so the
refusal can point at what was written. `EXPECT_ERROR_CODE` is a substring check, so the
code SET is pinned here.
"""
from __future__ import annotations

USED = """
perk Shown:
    fn show() i32

struct Box:
    i32 n

extend Box with Shown:
    fn show@(U)(U x) i32:
        return 1

fn main() i32:
    return Result.Ok(0)
"""

UNUSED = """
perk Hidden:
    fn h() i32

perk Shown:
    fn show() i32

struct Box:
    i32 n

extend Box with Shown:
    fn show@(U: Hidden)() i32:
        return 1

fn main() i32:
    return Result.Ok(0)
"""

EXTENSION_KEEPS_ITS_OWN = """
struct Box:
    i32 n

extend Box pick@(U)(U x) i32:
    return self.n

fn main() i32:
    let Box b = Box(3)
    println("{b.pick(1)}")
    return Result.Ok(0)
"""


def _codes(reporter):
    return [d.code for d in reporter.items if d.kind == "error"]


def test_an_implementation_method_may_not_declare_type_params(analyze):
    """One diagnostic at the list: the cascade about `U` was the old answer."""
    assert _codes(analyze(USED)) == ["CE4010"]


def test_an_unused_type_param_list_is_refused_too(analyze):
    """The signature matched the contract, so nothing else had anything to say."""
    assert _codes(analyze(UNUSED)) == ["CE4010"]


def test_a_plain_extension_method_keeps_its_type_params(analyze):
    """The refusal is the perk implementation's alone -- `map@(U)` is a feature."""
    assert _codes(analyze(EXTENSION_KEEPS_ITS_OWN)) == []
