"""Adding a `Drop` implementation must rebuild every unit, not just its own.

A perk implementation carries no visibility marker, so it is not a public symbol and no
consumer's `OWN_SYMBOLS` or `DEP_SYMBOLS` block sees it. But `extend File with Drop`
changes the OWNERSHIP CLASS of `File` for the whole program: a value that copied now
moves, and its scope exit now closes a descriptor.

Without the `DROP_TYPES` block in `compute_unit_fingerprint`, a consumer unit keeps a
`.o` compiled while the type was plain -- so the handle is copied and never closed, and
nothing in the build says so.
"""
from __future__ import annotations

from sushi_lang.compiler.fingerprint import compute_unit_fingerprint


class _Unit:
    """The two fields the fingerprint reads, for a unit with no file and no AST."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.file_path = _MissingPath()
        self.public_symbols: dict = {}
        self.dependencies: set = set()
        self.ast = None


class _MissingPath:
    def exists(self) -> bool:
        return False


def test_a_drop_implementation_moves_a_consumer_fingerprint():
    """The consumer unit is untouched; only the whole-program Drop set changed."""
    consumer = _Unit("consumer")

    before = compute_unit_fingerprint(consumer, drop_types=frozenset())
    after = compute_unit_fingerprint(consumer, drop_types=frozenset({"File"}))

    assert before != after, (
        "adding `extend File with Drop` left the consumer's fingerprint unchanged, so it "
        "keeps a .o built while File was a plain copyable value. The handle is copied "
        "and never closed, with no diagnostic."
    )


def test_the_set_is_order_independent():
    """Two units may register implementations in either order; the digest must not care."""
    consumer = _Unit("consumer")
    one = compute_unit_fingerprint(
        consumer, drop_types=frozenset({"File", "TcpStream", "TcpListener"}))
    other = compute_unit_fingerprint(
        consumer, drop_types=frozenset({"TcpListener", "File", "TcpStream"}))
    assert one == other


def test_removing_an_implementation_moves_it_back():
    """The digest tracks the set, so dropping the implementation invalidates again."""
    consumer = _Unit("consumer")
    plain = compute_unit_fingerprint(consumer, drop_types=frozenset())
    with_drop = compute_unit_fingerprint(consumer, drop_types=frozenset({"File"}))
    back = compute_unit_fingerprint(consumer, drop_types=frozenset())

    assert with_drop != plain
    assert back == plain


def test_an_unrelated_drop_type_still_moves_it():
    """The set is whole-program, so any change to it rebuilds every unit.

    Coarser than strictly necessary -- a unit that never names `TcpStream` is rebuilt
    when `TcpStream` gains a Drop. That is deliberate: the alternative is asking which
    types each unit can transitively reach, and a miss there is a leaked descriptor.
    """
    consumer = _Unit("consumer")
    one = compute_unit_fingerprint(consumer, drop_types=frozenset({"File"}))
    two = compute_unit_fingerprint(consumer, drop_types=frozenset({"File", "TcpStream"}))
    assert one != two
