"""The statement arms of the borrow pass over SHIR (docs/design/ir.md 7.5, 7.9).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`arms.ARMS`), and releases the `let`-borrows that the block opened. The
borrows of a statement end with the statement (`clear_borrows`).

An `Assign` of a local is a `let` (`is_init`) or a rebind. A `let` BINDS: it declares the
local, then takes its value by the decision of a consuming use (`bind`). A rebind
RE-INITIALIZES the local: it is refused through a view of another value's storage
(`reject_readonly_write`), it takes its value as a consuming use, and every fact about the
old value goes. A write of a field or of an element changes its root in place: it is
refused through a read-only view of the root, and it takes its value as a consuming use.
A destructure takes its value as a consuming use, declares its binders, and rebinds its
places from left to right. A `return` hands its value to the caller, a consuming use; the
`return` of a `peek` return hands a view (`borrowed_returns.py`). An expression statement
reads its value, and `x.destroy()` destroys the local `x`.

An `if` checks each block from the state before it, and the paths that reach the code
after it join. A `match` reads its scrutinee (`match nom r:` takes it), each arm declares
its bare binders, and its arms join as the blocks of an `if` do. A `while` loop checks its
condition once, then its body in two rounds (`check_loop_body`): the paths of a
`continue` and of the end of the body go round the back edge, and the paths of a `break`
leave the loop. A counted loop reads the bounds of its range once, and declares its binder
as a read-only view before its body. A direct walk has its own arm (`walks.py`). A loop over
an `IteratorValue` too. A loop over any other iterable stops the walk. The end of a block that the path
reaches, a `return`, a `break` and a `continue` are exits of the blocks that they leave
(`flow.record_exit`), and a rebind is an exit of the old value of its local
(`flow.record_rebind`).
"""

from __future__ import annotations
from functools import partial
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span

from sushi_lang.semantics.ownership import Provenance, TypeClass
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.loop_kind import (
    CountedRange, Cursor, HashMapCursor, IteratorValue)
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.paths import reached_statements, terminates
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.typesys import ReferenceType

from . import arms
from .bindings import (
    bare_binders, declare_bare_binders, reference_binders, release_binding_borrow)
from .pattern_takes import (
    declare_taken_binders, owns_scrutinee, reject_partial_take, reject_take_from_a_borrow,
    taken_binders)
from .borrowed_returns import check_borrowed_return
from .borrows import clear_borrows
from .consume import (
    bind, binds_a_bare_literal_string, consume, decide, settle_function_value,
    settle_rebound_function_value, source_provenance, value_owners)
from .diagnostics import emit_consume_of_borrow, spelled
from .expressions import reject_a_use_after_the_change
from .flow import (
    FlowFacts,
    LoopFrame,
    record_exit,
    record_rebind,
    reinitialize,
    restore_flow,
    snapshot_flow,
)
from .reads import borrow_owner, read_type
from .references import check_let_reference, declare_reference_binders
from .regions import note_part_view, note_view
from .state import BorrowState, record_owners
from .walks import check_direct_walk, check_value_walk
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker
    from .state import Owner


def check_block(checker: 'BorrowChecker', block: ShirBlock,
                declared: Sequence[LocalId] = (),
                frozen: Sequence[tuple[Owner, Owner]] = ()) -> None:
    """Check a block, releasing the `let`-borrows it opened on the way out. `declared` are
    the locals of the block that come before its statements: the `nom` binders of an arm.
    `frozen` are the owners that the binders of an arm froze, which the end of the arm
    releases.

    The walk reads each statement that a path reaches (`reached_statements`).
    """
    if checker.body is None:
        er.raise_internal_error("CE0000", span=block.loc,
                                detail="the borrow pass walks a block of no body")
    checker._scope_binding_borrows.append(list(frozen))
    checker.block_locals.append(list(declared))
    checker.statement_at.append((block, 0))
    try:
        for index, statement in reached_statements(block, checker.body):
            checker.statement_at[-1] = (block, index)
            arms.check(checker, statement)
        if not terminates(block, leaves_round=True):
            record_exit(checker, len(checker.block_locals) - 1)
    finally:
        checker.block_locals.pop()
        checker.statement_at.pop()
        for owner, binding in checker._scope_binding_borrows.pop():
            release_binding_borrow(checker.borrow_state.get(owner), binding)


def check_assign(checker: 'BorrowChecker', node: s.Assign) -> None:
    if isinstance(node.target, (e.Field, e.Index)):
        _check_place_write(checker, node, node.target)
    elif isinstance(node.target, e.StorageRef):
        _check_storage_rebind(checker, node)
    elif not isinstance(node.target, e.LocalRef):
        arms.stop_at(checker, node)
    elif node.is_init:
        _check_let(checker, node, node.target.local)
    else:
        _check_rebind(checker, node, node.target.local)
    clear_borrows(checker)


def _check_let(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`let T x = v`: declare the local, then give it the provenance of its value. A
    reference `let` points into its place (`references.py`)."""
    shir_local = checker.locals.get(local)
    if shir_local is not None and isinstance(shir_local.declared, ReferenceType):
        check_let_reference(checker, node, local)
        return
    ty = checker.local_type(local)
    state = BorrowState(name=checker.name_of(local), local=local, var_type=ty,
                        declared_at_span=node.loc,
                        # Option B: a string bound straight from a literal owns no heap,
                        # so consuming it transfers nothing and CE2405 must not fire on it.
                        owns_no_heap=binds_a_bare_literal_string(ty, node.value))
    checker.declare(state)
    arms.check(checker, node.value)
    bind(checker, node, state)
    settle_function_value(checker, state, node.value)
    note_view(checker, node, state)
    # The local is in its scope from here: an exit in its value comes before it.
    checker.block_locals[-1].append(local)


def _check_rebind(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`x := v`: the local takes ownership of a new value."""
    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    if _reject_readonly_rebind(checker, node.target, node.loc, node.value):
        return
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, local, node.loc, "assign", place=node.target)
    consume(checker, node.value)
    _reinitialize(checker, local)
    state = checker.borrow_state.get(local)
    if state is not None:
        settle_rebound_function_value(checker, state, node.value)


def _check_storage_rebind(checker: 'BorrowChecker', node: s.Assign) -> None:
    """`v := x` of a `var`: storage with an address of its own, so the rebind is legal in
    every body, and the value is a consuming use."""
    arms.check(checker, node.value)
    consume(checker, node.value)


def _reject_readonly_rebind(checker: 'BorrowChecker', target: e.LocalRef,
                            span: Optional[Span], value: Optional[e.ShirExpr]) -> bool:
    """The gates of a rebind of a local, before its value. `value` is None for a place of a
    destructure, which takes an element and never a literal. True when the rebind of a lent
    iterator is refused: that is the one fault, and the pass does not judge its value."""
    state = checker.borrow_state.get(target.local)
    if state is None:
        return False
    # THE gate, in the rebind position: a name that is a view of another value's storage
    # cannot be rebound, a name with storage of its own can. A `poke` reference is the
    # middle case and stays legal -- the store goes through the pointer, which is what
    # the mode is for.
    refused = reject_readonly_write(checker, target.local, span, "rebind the name",
                                    rebind=True)
    if refused and state.is_lent_iterator:
        return True
    # The store goes through the pointer, so it is a USE of the reference: after a change
    # of its owner it writes into storage the owner no longer holds.
    if not refused and isinstance(state.var_type, ReferenceType):
        reject_a_use_after_the_change(checker, target.local, target.loc)
    # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this flag, never set it
    # on a value that owns heap.
    state.owns_no_heap = binds_a_bare_literal_string(state.var_type, value)
    return False


def _reinitialize(checker: 'BorrowChecker', local: LocalId) -> None:
    """The rebind drops the old value, so it is an exit of that value: one moved on some
    paths only needs the drop flag (`flow.record_rebind`). A rebind RE-INITIALIZES, so a
    previous move no longer holds. The value was checked before, so `s := "{s}-x"` still
    reports the moved `s`."""
    record_rebind(checker, local)
    state = checker.borrow_state.get(local)
    if state is not None:
        reinitialize(state)


def _check_place_write(checker: 'BorrowChecker', node: s.Assign,
                       target: e.Field | e.Index) -> None:
    """`p.x := v` and `a[i] := v`: a write of a field or of an element changes its root in
    place, and takes the value as a consuming use."""
    _reject_readonly_place_write(checker, target, node.loc)
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, borrow_owner(checker, target), node.loc, "assign",
                             place=target)
    consume(checker, node.value)


def _reject_readonly_place_write(checker: 'BorrowChecker', target: e.Field | e.Index,
                                 span: Optional[Span]) -> None:
    """The gate of a write of a field or of an element, then the read of the place. A write
    is allowed unless its root is a read-only view, where the store cannot reach what it
    writes, a temporary (CE2429), or a slice (CE2426)."""
    if isinstance(target, e.Field):
        what = "assign to a field"
    else:
        what = "assign to an array element"
    reject_readonly_write(checker, borrow_owner(checker, target), span, what, receiver=target.base)
    arms.check(checker, target)


def check_destructure(checker: 'BorrowChecker', node: s.Destructure) -> None:
    """`let (a, b) = v` and `(a, b) := v` (docs/design/ir.md 7.5, 7.9).

    The value is checked first, and it is a consuming use. A value of plain elements is a
    copy. A value that owns is OWNED or BORROWED, by its provenance: an owned value (a new
    value, or a local that the body owns) is taken whole, and each binder owns its element.
    A borrowed value in the `let` form makes each binder a borrowed view of its element,
    which freezes the owners of the value while it lives; a binder of an owning element has
    a borrow region. A `let` declares each binder as a local of its own, at any nesting
    level. A rebind form then rebinds each place from left to right, through the gates of
    a rebind of a local or of a write of a field, at the span of the place; from a borrowed
    value, each place of an owning element is the consuming use of a borrow (CE2411).
    """
    arms.check(checker, node.value)
    targets = _targets(node.targets)
    owns = checker.types.type_class(read_type(checker, node.value)) is TypeClass.MOVE
    borrowed = owns and source_provenance(checker, node.value) is Provenance.BORROWED
    if borrowed:
        decide(checker, node.value, Provenance.BORROWED, TypeClass.MOVE)
    else:
        consume(checker, node.value)
    for path, target in targets:
        if isinstance(target, s.Place):
            _rebind_place(checker, target.target)
            if borrowed:
                _reject_take_of_a_part(checker, node, path, target.target)
    owners = view_owners_of_value(checker, node.value) if borrowed else None
    for _path, target in targets:
        if not isinstance(target, s.Place):
            _declare_binder(checker, target, node, owners)
    clear_borrows(checker)


def _targets(targets: Sequence[s.DestructureTarget], prefix: tuple[int, ...] = ()
             ) -> list[tuple[tuple[int, ...], LocalId | s.Place]]:
    """The binders and the places of a destructure, from left to right, at any nesting
    level, each with its path. A `_` binds nothing."""
    found: list[tuple[tuple[int, ...], LocalId | s.Place]] = []
    for index, target in enumerate(targets):
        if isinstance(target, s.Nested):
            found.extend(_targets(target.targets, (*prefix, index)))
        elif not isinstance(target, s.Discard):
            found.append(((*prefix, index), target))
    return found


def view_owners_of_value(checker: 'BorrowChecker', value: e.ShirExpr) -> tuple:
    """The owners that each binder of a borrowed value freezes."""
    return value_owners(checker, value)


def _reject_take_of_a_part(checker: 'BorrowChecker', node: s.Destructure,
                           path: tuple[int, ...], place: e.ShirExpr) -> None:
    """CE2411 at a place of a rebind form that takes an owning element of a borrowed value:
    the element is named as the value and its path (`p.0`), as for `a := p.0`."""
    element = checker.answers().element_type_of(node.shir_id, path)
    if checker.types.type_class(element) is not TypeClass.MOVE:
        return
    name = ".".join([spelled(checker, node.value), *map(str, path)])
    emit_consume_of_borrow(checker, name, place.loc,
                           BorrowState(name=name, var_type=element, bound_at_span=node.loc))


def declare_binders(checker: 'BorrowChecker', node: s.Destructure) -> None:
    """Declare each binder of a destructure that has a type, and walk nothing: the
    destructure is unknown in a body with an error of typecheck (docs/design/ir.md 7.9)."""
    for _path, target in _targets(node.targets):
        if not isinstance(target, s.Place) and checker.local_type(target) is not None:
            _declare_binder(checker, target, node, None)


def _declare_binder(checker: 'BorrowChecker', local: LocalId, node: s.Destructure,
                    owners: Optional[tuple]) -> None:
    """A binder of a destructure: a local of its own, or with `owners` a borrowed view of
    its element, which freezes those owners; a view of an owning element has a borrow
    region from the destructure on."""
    shir_local = checker.locals.get(local)
    ty = checker.local_type(local)
    state = BorrowState(name=checker.name_of(local), local=local, var_type=ty,
                        declared_at_span=shir_local.loc if shir_local else None)
    checker.declare(state)
    checker.block_locals[-1].append(local)
    if owners is None:
        return
    state.is_borrowed_binding = True
    state.is_let_borrow = True
    state.bound_at_span = node.loc
    record_owners(checker.borrow_state, state, owners, node.loc,
                  checker._scope_binding_borrows[-1])
    if checker.types.type_class(ty) is TypeClass.MOVE:
        note_part_view(checker, state)


def _rebind_place(checker: 'BorrowChecker', place: e.ShirExpr) -> None:
    """A place of a rebind form takes its element: a local is rebound, and a field and an
    element are written. Typecheck gives a place of no other kind no answer, and stops
    first."""
    if isinstance(place, (e.Field, e.Index)):
        _reject_readonly_place_write(checker, place, place.loc)
        check_owner_not_borrowed(checker, borrow_owner(checker, place), place.loc, "assign",
                                 place=place)
        return
    if isinstance(place, e.StorageRef):
        return
    if not isinstance(place, e.LocalRef):
        arms.stop_at(checker, place)
    _reject_readonly_rebind(checker, place, place.loc, None)
    check_owner_not_borrowed(checker, place.local, place.loc, "assign", place=place)
    _reinitialize(checker, place.local)


def check_return(checker: 'BorrowChecker', node: s.Return) -> None:
    """A `return` hands its value to the caller: a consuming use. A `peek` body hands a
    VIEW to the caller, and the value must be a borrow of a source (`borrowed_returns.py`)."""
    if checker.borrowed_return is not None:
        check_borrowed_return(checker, node.value)
    else:
        arms.check(checker, node.value)
        consume(checker, node.value)
    clear_borrows(checker)
    record_exit(checker)


def check_expr_stmt(checker: 'BorrowChecker', node: s.ExprStmt) -> None:
    """A value that the statement reads and drops. `x.destroy()` releases what the local
    `x` holds, so every later use of `x` is CE2406."""
    arms.check(checker, node.value)
    value = node.value
    if (isinstance(value, e.Call) and isinstance(value.target, e.Dotted)
            and value.target.name == "destroy" and isinstance(value.target.recv, e.LocalRef)):
        state = checker.borrow_state.get(value.target.recv.local)
        if state is not None:
            state.is_destroyed = True
    clear_borrows(checker)


def check_assert(checker: 'BorrowChecker', node: s.Assert) -> None:
    """The condition is read at the entry. The message is read only on the failing path, so
    the path that holds skips it and the two paths join."""
    arms.check(checker, node.cond)
    clear_borrows(checker)
    if node.message is None:
        return
    entry = snapshot_flow(checker)
    arms.check(checker, node.message)
    clear_borrows(checker)
    restore_flow(checker, FlowFacts.join([entry, snapshot_flow(checker)]))


def join_exclusive_paths(
        checker: 'BorrowChecker', blocks: Sequence[ShirBlock | None],
        enter: Optional[Callable[[int], tuple[Sequence[LocalId],
                                              Sequence[tuple[Owner, Owner]]]]] = None,
) -> None:
    """Check exclusive blocks, each from the state before them; the paths that reach the
    code after them JOIN. `None` is a path that skips every block. `enter` runs at the
    start of each path, with the index of its block: an arm of a match declares its
    binders there, and answers the binders that are locals of its block and the owners
    that its binders froze.

    A local is moved after the join when it is moved on ANY path, and moved on every path
    when it is moved on each one. Only the paths that reach the code after the blocks
    contribute: a block that ends in a `return`, a `break` or a `continue` leaves by its
    own exit, so its move reaches no other block and no statement below (#287).
    """
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for index, block in enumerate(blocks):
        restore_flow(checker, entry)
        if block is None:
            paths.append(entry)
            continue
        declared, frozen = enter(index) if enter is not None else ((), ())
        check_block(checker, block, declared, frozen)
        if not terminates(block, leaves_round=True):
            paths.append(snapshot_flow(checker))
    restore_flow(checker, FlowFacts.join(paths))


def check_if(checker: 'BorrowChecker', node: s.If) -> None:
    """The two blocks are exclusive paths. With no `else`, the path that skips the `then`
    block is a path too. An `elif` is an `if` in the `else` block."""
    arms.check(checker, node.cond)
    clear_borrows(checker)
    join_exclusive_paths(checker, (node.then, node.otherwise))


def check_match(checker: 'BorrowChecker', node: s.Match) -> None:
    """A match reads its scrutinee, and `match nom r:` takes it over, a consuming use. The
    arms are exclusive paths, and they join as the blocks of an `if` do. The arms cover
    each value (the typecheck pass), so no path skips them. Each arm declares its binders
    before its body: the bare binders, the `peek` and `poke` binders (`references.py`), and
    the `nom` binders, which take their payload out of a scrutinee that the match owns
    (`pattern_takes.py`). The owners that the binders of an arm freeze are released at the
    end of the arm. A tuple literal that owns, as the scrutinee, builds no tuple, and each
    part is a root of its own."""
    binders = [bare_binders(arm.pattern) for arm in node.arms]
    if any(found is None for found in binders):
        arms.stop_at(checker, node)
    references = [reference_binders(arm.pattern) for arm in node.arms]
    roots = in_place_roots(checker, node)
    for root in roots or (node.scrutinee,):
        arms.check(checker, root)
        if node.consumes:
            consume(checker, root)
    owners = {local: _view_owners_of(checker, node, root)
              for arm in node.arms for local, root in _binder_roots(node, arm.pattern, roots)}
    clear_borrows(checker)

    def enter(index: int) -> tuple[Sequence[LocalId], Sequence[tuple[Owner, Owner]]]:
        arm = node.arms[index]
        for root, item in _positions(node, arm.pattern, roots):
            reject_partial_take(checker, item, read_type(checker, root))
            if not owns_scrutinee(checker, node, root):
                for local in taken_binders(item):
                    reject_take_from_a_borrow(checker, local, root)
        frame: list[tuple[Owner, Owner]] = []
        declare_bare_binders(checker, binders[index] or (), owners, arm.body, frame)
        declare_reference_binders(checker, node, references[index],
                                  dict(_binder_roots(node, arm.pattern, roots)), arm.body,
                                  frame)
        return declare_taken_binders(checker, taken_binders(arm.pattern)), frame
    join_exclusive_paths(checker, [arm.body for arm in node.arms], enter=enter)


def _positions(node: s.Match, pattern: p.ShirPattern, roots: tuple[e.ShirExpr, ...]
               ) -> list[tuple[e.ShirExpr, p.ShirPattern | p.PatBinding]]:
    """Each scrutinee that an arm reads, with the item of the pattern at it: the scrutinee
    and the whole pattern, or each part of an in-place tuple literal with the item at its
    position (the alternatives of `|` give each part the alternatives of its own position).
    A pattern with no variant and no tuple takes nothing, and has no position."""
    alternatives = pattern.alternatives if isinstance(pattern, p.Or) else [pattern]
    if not any(isinstance(alt, (p.Variant, p.Tuple)) for alt in alternatives):
        return []
    if not roots:
        return [(node.scrutinee, pattern)]
    tuples = [alt for alt in alternatives if isinstance(alt, p.Tuple)]
    positions: list[tuple[e.ShirExpr, p.ShirPattern | p.PatBinding]] = []
    for index, root in enumerate(roots):
        items = [alt.elements[index] for alt in tuples if index < len(alt.elements)]
        if len(items) == 1:
            positions.append((root, items[0]))
        elif items:
            positions.append((root, p.Or(pattern.shir_id, pattern.loc, items)))
    return positions


def in_place_roots(checker: 'BorrowChecker', node: s.Match) -> tuple[e.ShirExpr, ...]:
    """The parts of a tuple literal that owns, as the scrutinee: the match builds no tuple,
    and each part is a root of its own (ruling 3 of docs/design/tuples.md). None for any
    other scrutinee."""
    scrutinee = node.scrutinee
    if isinstance(scrutinee, e.TupleInit) and checker.types.type_class(
            read_type(checker, scrutinee)) is TypeClass.MOVE:
        return tuple(scrutinee.parts)
    return ()


def _binder_roots(node: s.Match, pattern: p.ShirPattern, roots: tuple[e.ShirExpr, ...]
                  ) -> list[tuple[LocalId, e.ShirExpr]]:
    """Each bare binder of an arm with the root that it views: the part of an in-place
    tuple literal that holds it, else the scrutinee."""
    if isinstance(pattern, p.Or):
        pattern = pattern.alternatives[0]
    if roots and isinstance(pattern, p.Tuple):
        return [(local, root) for root, item in zip(roots, pattern.elements, strict=True)
                for local in _arm_binders(item, pattern.loc)]
    return [(local, node.scrutinee) for local in _arm_binders(pattern, None)]


def _arm_binders(pattern: p.ShirPattern | p.PatBinding, at: Optional[Span]
                   ) -> list[LocalId]:
    """The bare binders and the reference binders of a pattern."""
    return ([local for local, _at in bare_binders(pattern, at) or ()]
            + [local for local, _mode, _at in reference_binders(pattern)])


def _view_owners_of(checker: 'BorrowChecker', node: s.Match, root: e.ShirExpr) -> tuple:
    """The owners that a binder freezes: none when the match takes its root."""
    return () if node.consumes else value_owners(checker, root)


def check_loop(checker: 'BorrowChecker', node: s.Loop) -> None:
    """`while (cond)`: the condition is read at the entry, then the body is checked to its
    fixed point, and each view that the condition reads is read again at the back edge: a
    change of its owner in the body is CE2412 there. A counted loop reads the bounds of its range once, at the entry,
    and its binder is a read-only view, as a bare binder of a match is. A direct walk has
    its own arm (`walks.py`), and so does an `IteratorValue` walk. A loop over any other
    iterable stops the walk: the iterators group checks it."""
    source = node.source
    again: Optional[Callable[[], None]] = None
    kind = checker.answers().loop_kind_of(node.shir_id)
    if isinstance(source, s.Cond):
        arms.check(checker, source.cond)
        again = partial(_read_views_again, checker, source.cond)
    elif isinstance(kind, (Cursor, HashMapCursor)):
        check_direct_walk(checker, node, source)
        return
    elif isinstance(kind, IteratorValue):
        check_value_walk(checker, node, source)
        return
    elif isinstance(kind, CountedRange) and isinstance(source.iterable, e.Range):
        arms.check(checker, source.iterable.start)
        arms.check(checker, source.iterable.end)
        if not isinstance(source.binder, s.Discard):
            binder = checker.locals.get(source.binder)
            at = node.loc if binder is None or binder.loc is None else binder.loc
            declare_bare_binders(checker, [(source.binder, at)])
    else:
        arms.stop_at(checker, node)
    clear_borrows(checker)
    check_loop_body(checker, node.body, again)


def check_expand(checker: 'BorrowChecker', node: s.Expand) -> None:
    """An `expand` of a template check, one time for every length of the pack (ir.md 4.5).

    The body runs zero or more times, as the body of a loop does, so a move of an outer value
    in it is a move in a loop. It is no loop of its own: a `break` or a `continue` in it goes
    to the enclosing loop. The binder is one element of the value pack, a borrow of the value
    of the caller, because a value pack has no `nom` form."""
    binder = checker.locals[node.binder]
    checker.declare(BorrowState(name=binder.name, local=node.binder,
                                var_type=checker.local_type(node.binder),
                                declared_at_span=binder.loc or node.loc,
                                is_borrow_param=True))
    check_loop_body(checker, node.body, per_round=frozenset({node.binder}), leaves_to_outer=True)


def _read_views_again(checker: 'BorrowChecker', cond: e.ShirExpr) -> None:
    """The condition of a `while` loop at the back edge: each local that it reads is a use
    again, so a view whose owner the body changed is CE2412, one time for each view."""
    seen: set[LocalId] = set()
    for _path, value in walk(cond, ""):
        if isinstance(value, e.LocalRef) and value.local not in seen:
            seen.add(value.local)
            reject_a_use_after_the_change(checker, value.local, value.loc)


def check_loop_body(checker: 'BorrowChecker', body: ShirBlock,
                    again: Optional[Callable[[], None]] = None,
                    *, per_round: frozenset[LocalId] = frozenset(),
                    walk: Optional[BorrowState] = None,
                    leaves_to_outer: bool = False) -> None:
    """Check a loop body to a fixed point, so that the back edge is honoured.

    The first round discovers the facts that come round the back edge: it reports no
    error, writes no row and records no exit. The second round starts from the entry
    joined with that back edge, and reports. The code after the loop sees the fixed point
    (the condition is false) joined with each `break` path of the second round (#993).
    `again` reads the condition of the loop at the back edge of the second round.
    `per_round` holds the locals that each round makes again, so their facts do not go round
    the back edge. `walk` is the state of a direct walk (`walks.py`): when a move of its
    owner comes round the back edge, the walk reports it, one time. With `leaves_to_outer`
    (the body of an `expand`) a `break` and a `continue` belong to the enclosing loop.
    """
    outer = checker.loop_frames[-1] if leaves_to_outer and checker.loop_frames else None
    entry = snapshot_flow(checker)
    suppressed = checker.err.suppressed
    checker.err.suppressed = True
    checker.discovery_rounds += 1
    try:
        first_back, _first_breaks = _check_round(checker, body, per_round, leaves_to_outer)
    finally:
        checker.discovery_rounds -= 1
        checker.err.suppressed = suppressed
    fixed_point = entry if first_back is None else entry | first_back
    restore_flow(checker, fixed_point)
    _leave_to_the_items(checker, walk, first_back, per_round)
    reported = _move_reported_by_walk(checker, walk, entry, first_back)
    if reported is not None and walk is not None:
        reported.move_reported_by = walk.local
    try:
        back, breaks = _check_round(checker, body, per_round, leaves_to_outer, outer)
    finally:
        if reported is not None:
            reported.move_reported_by = None
    if again is not None and back is not None:
        restore_flow(checker, back)
        again()
    restore_flow(checker, fixed_point if breaks is None else fixed_point | breaks)


def _leave_to_the_items(checker: 'BorrowChecker', walk: Optional[BorrowState],
                        first_back: FlowFacts | None, items: frozenset[LocalId]) -> None:
    """A change that does not reach the next round does not meet the walk again, so the walk
    covers no item for it: a use of the item after the change is CE2412 at that use."""
    if walk is None or (first_back is not None
                        and first_back.invalidation_of(walk.local) is not None):
        return
    for local in items:
        checker.borrow_state[local].covered_by = None


def _move_reported_by_walk(checker: 'BorrowChecker', walk: Optional[BorrowState],
                           entry: FlowFacts, first_back: FlowFacts | None
                           ) -> Optional[BorrowState]:
    """The owner whose move came round the back edge and changed under `walk`, or None."""
    if walk is None or first_back is None:
        return None
    change = first_back.invalidation_of(walk.local)
    if change is None:
        return None
    _span, (owner, _what) = change
    if owner not in first_back.moved or owner in entry.moved:
        return None
    return checker.borrow_state.get(owner)


def _check_round(checker: 'BorrowChecker', body: ShirBlock,
                 per_round: frozenset[LocalId] = frozenset(),
                 leaves_to_outer: bool = False, outer: Optional[LoopFrame] = None
                 ) -> tuple[FlowFacts | None, FlowFacts | None]:
    """Check one round of a loop body: the join of the paths to the back edge, and the
    join of the `break` paths, with no fact about `per_round`. Each is None when no path of
    that kind exists, because `FlowFacts.join` of no path is no identity. With
    `leaves_to_outer`, the `break` and `continue` paths go to `outer` (none in a discovery
    round), and the round has only the path to its end."""
    frame = LoopFrame(len(checker.block_locals))
    checker.loop_frames.append(frame)
    try:
        check_block(checker, body, declared=tuple(per_round))
    finally:
        checker.loop_frames.pop()
    if leaves_to_outer:
        if outer is not None:
            outer.breaks.extend(path.without(per_round) for path in frame.breaks)
            outer.continues.extend(path.without(per_round) for path in frame.continues)
        frame = LoopFrame(frame.blocks)
    back = list(frame.continues)
    if not terminates(body, leaves_round=True):
        back.append(snapshot_flow(checker))
    return (FlowFacts.join(back).without(per_round) if back else None,
            FlowFacts.join(frame.breaks).without(per_round) if frame.breaks else None)


def check_break(checker: 'BorrowChecker', node: s.Break) -> None:
    """A `break` path leaves the loop: it joins the code after the loop."""
    _innermost_loop(checker, node).breaks.append(_leave_round(checker, node))


def check_continue(checker: 'BorrowChecker', node: s.Continue) -> None:
    """A `continue` path goes round the back edge to the next round."""
    _innermost_loop(checker, node).continues.append(_leave_round(checker, node))


def _leave_round(checker: 'BorrowChecker', node: s.Break | s.Continue) -> FlowFacts:
    """The exit of each block of the loop body, and the facts of the path."""
    record_exit(checker, _innermost_loop(checker, node).blocks)
    return snapshot_flow(checker)


def _innermost_loop(checker: 'BorrowChecker', node: s.Break | s.Continue) -> LoopFrame:
    if not checker.loop_frames:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"a '{type(node).__name__}' outside a loop reached "
                                       f"the borrow pass; the typecheck pass refuses it")
    return checker.loop_frames[-1]
