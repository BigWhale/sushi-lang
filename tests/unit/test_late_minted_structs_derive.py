"""A struct minted AFTER the derive pass gets its pair from the seam that mints it (#730).

The derive pass walks both tables once, before typecheck. Two producers mint a struct
behind it, and each was measured on its own:

`Entry<K, V>` (`generics/hashmap.py`) is an ordinary user-facing struct -- a `foreach`
item whose `.key` and `.value` a body reads. Nothing but the MINT TIME kept its derived
pair away, which is the accident #720 and #721 name, so the seam that interns it derives
the pair there.

`__closure_env_N` (`passes/lift.py`) is the deliberate absence, the shape `List`, `Own`
and `HashMap` already have in `generics/cloning.py`: the environment is a heap box with
its own duplicator and destructor (`backend/runtime/closures.py`,
`get_or_create_env_clone` / `get_or_create_env_drop`), reached through the closure fat
pointer's `clone_ptr` and `drop_ptr` slots and never through this table. The name is
unwritable, so no receiver can reach an entry either. A census of the derived table reads
an environment struct as empty BY DESIGN and not as a registration that went missing.
"""
from __future__ import annotations

from sushi_lang.semantics.derived_methods import BuiltinMethodRegistry

ENTRIES = """
use <collections/hashmap>

fn main() i32:
    let HashMap@(i32, string) m = HashMap.new()
    m.insert(1, "one")
    foreach(e in m.entries()):
        println("{e.key}: {e.value}")
    m.free()
    return Result.Ok(0)
"""

CAPTURING_CLOSURE = """
fn apply(fn(i32) -> i32 f, i32 v) i32:
    return Result.Ok(f(v)??)

fn main() i32:
    let string tag = "count"
    let i32 base = 7
    let fn(i32) -> i32 add = |i32 n|:
        println(tag)
        return Result.Ok(n + base)
    let i32 got = apply(add, 1).realise(0)
    println("{got}")
    return Result.Ok(0)
"""


def _own(analysis, target, method: str):
    """This compilation's derived method, past the process-wide primitive built-ins."""
    derived = analysis.analyzer.tables.derived_methods
    return BuiltinMethodRegistry.get_method(derived, target, method)


def _struct(analysis, prefix: str):
    """The one struct of the table whose name starts with `prefix`."""
    structs = analysis.analyzer.tables.structs
    named = [name for name in structs.order if name.startswith(prefix)]
    assert named, f"no struct named '{prefix}...' was minted"
    return structs.by_name[named[0]]


def test_an_entry_struct_carries_both(analyze_program):
    """`.entries()` interns `Entry<K, V>` after derive, and the mint supplies the pair."""
    analysis = analyze_program(ENTRIES, name="entries")
    entry = _struct(analysis, "Entry<")

    assert _own(analysis, entry, "hash") is not None
    assert _own(analysis, entry, "clone") is not None


def test_a_closure_environment_carries_neither(analyze_program):
    """The deliberate absence: the environment keeps its own duplicator and destructor."""
    analysis = analyze_program(CAPTURING_CLOSURE, name="closure")
    env = _struct(analysis, "__closure_env_")

    assert _own(analysis, env, "hash") is None
    assert _own(analysis, env, "clone") is None
