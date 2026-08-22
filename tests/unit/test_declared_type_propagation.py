"""Every DECLARED type position propagates into the value it types (issue #387).

A generic enum or struct constructor carries no type of its own: the typecheck pass must stamp
`resolved_enum_type` / `resolved_struct_type` on it from the position's declared type, and
the backend reports **CE0113** for one that arrives unstamped. The extension and perk
callables had three positions that stamped nothing -- the return value, and an argument at
each of the two method-call arms -- so this gate asks the question per (callable kind,
position) rather than per symptom.

The declared type is a SPELLING: `Maybe@(i32)` reaches these positions as a
`GenericTypeRef`, which matches no arm of `propagate_types_to_value`. So a site that
propagates without resolving first stamps nothing, and passes an "it propagates" test that
only checks the call is made -- which is why this gate reads the stamp off the AST.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ast import Call, DotCall, EnumConstructor, Name, Node

_PRELUDE = """\
struct P:
    i32 n

struct Pair@(T, U):
    T first
    U second

enum Box@(T):
    Wrap(T)
    Empty
"""

_TAIL = """\
fn main() i32:
    return Result.Ok(0)
"""

# (id, source body). Each body puts ONE generic constructor in ONE declared position.
_CASES = [
    (
        "extension_return_maybe",
        """\
extend i32 halved() Maybe@(i32):
    return Maybe.Some(self / 2)
""",
    ),
    (
        "extension_return_user_enum",
        """\
extend i32 boxed() Box@(i32):
    return Box.Wrap(self)
""",
    ),
    (
        "extension_return_user_struct",
        """\
extend i32 paired() Pair@(i32, i32):
    return Pair(self, self)
""",
    ),
    (
        "perk_return_maybe",
        """\
perk Halver:
    fn half() Maybe@(i32)

extend i32 with Halver:
    fn half() Maybe@(i32):
        return Maybe.Some(self / 2)
""",
    ),
    (
        "extension_argument_maybe",
        """\
extend i32 pick(Maybe@(i32) m) i32:
    return self + m.realise(0)

fn use_it() i32:
    return Result.Ok(1.pick(Maybe.Some(41)))
""",
    ),
    (
        "extension_argument_user_struct",
        """\
extend i32 sum_pair(Pair@(i32, i32) p) i32:
    return self + p.first + p.second

fn use_it() i32:
    return Result.Ok(2.sum_pair(Pair(4, 6)))
""",
    ),
    (
        "perk_argument_maybe",
        """\
perk Picker:
    fn choose(Maybe@(i32) m) i32

extend i32 with Picker:
    fn choose(Maybe@(i32) m) i32:
        return self + m.realise(1)

fn use_it() i32:
    return Result.Ok(1.choose(Maybe.Some(41)))
""",
    ),
    # Controls: the two positions that always propagated. They pin that this gate reads
    # the same stamp the working paths write, so a green row here is evidence about the
    # gate, not only about the fix.
    (
        "plain_function_return_maybe",
        """\
fn halved(i32 n) Maybe@(i32):
    return Result.Ok(Maybe.Some(n / 2))
""",
    ),
    (
        "plain_function_argument_maybe",
        """\
fn pick(Maybe@(i32) m) i32:
    return Result.Ok(m.realise(0))

fn use_it() i32:
    return Result.Ok(pick(Maybe.Some(41))??)
""",
    ),
]

_GENERIC_NAMES = {"Maybe", "Box", "Pair"}


def _walk(node, seen=None):
    """Every AST node reachable from `node`, once each."""
    if seen is None:
        seen = set()
    if not isinstance(node, Node) or id(node) in seen:
        return
    seen.add(id(node))
    yield node

    for value in vars(node).values():
        if isinstance(value, (list, tuple)):
            for item in value:
                yield from _walk(item, seen)
        else:
            yield from _walk(value, seen)


def _generic_constructors(program):
    """Every generic constructor node in the program, with what propagation left on it.

    The two kinds are told apart differently, and that asymmetry is the existing design:
    an enum constructor carries `resolved_enum_type`, while a `Call`-form struct
    constructor is MONOMORPHIZED IN PLACE -- propagation rewrites its callee to the
    interned name (`Pair` -> `Pair<i32, i32>`), which is what makes the constructor
    resolvable at all. A bare `Pair` is the unpropagated state, and reported CE2008.
    """
    found = []
    for node in _walk(program):
        if isinstance(node, EnumConstructor) and node.enum_name in _GENERIC_NAMES:
            found.append((node.enum_name, getattr(node, "resolved_enum_type", None)))
        elif (isinstance(node, DotCall) and isinstance(node.receiver, Name)
                and node.receiver.id in _GENERIC_NAMES):
            found.append((node.receiver.id, getattr(node, "resolved_enum_type", None)))
        elif (isinstance(node, Call) and isinstance(node.callee, Name)
                and node.callee.id.split("<")[0] in _GENERIC_NAMES):
            interned = "<" in node.callee.id
            stamp = getattr(node, "resolved_struct_type", None) or (
                node.callee.id if interned else None)
            found.append((node.callee.id, stamp))
    return found


@pytest.mark.parametrize("case_id,body", _CASES, ids=[c[0] for c in _CASES])
def test_declared_position_stamps_its_constructor(analyze_program, case_id, body):
    """A generic constructor in a declared position leaves the typecheck pass with its type stamped."""
    analysis = analyze_program(_PRELUDE + body + _TAIL, name=case_id)

    assert not analysis.reporter.has_errors, (
        f"{case_id}: semantic analysis reported an error:\n"
        + "\n".join(str(d) for d in analysis.reporter.diagnostics)
    )

    constructors = _generic_constructors(analysis.program)
    assert constructors, f"{case_id}: no generic constructor found -- the case is inert"

    unstamped = [name for name, stamp in constructors if stamp is None]
    assert not unstamped, (
        f"{case_id}: {unstamped} left the typecheck pass with no resolved type. "
        "The backend reports CE0113 for exactly this."
    )
