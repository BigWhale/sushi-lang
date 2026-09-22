"""One selector decides which fixtures a run covers, and a run that selects none fails.

Three flags narrow a run -- `--filter`, `--leaks-only` and `--compile-only` -- and each
used to narrow a different list in a different place. `select_fixtures` is the one home:
`collect_fixtures` finds every fixture, `select_fixtures` says which of them THIS run
covers, and the runner asks no other question.

A selection that is EMPTY is a failure, not a pass (#765). The enhanced runner used to
answer an empty result dict, which `main` read as "nothing failed" and reported as exit 0 --
so a chunk that ran nothing was indistinguishable from a chunk that passed, in the mode CI
and the wave protocol trust. That is the shape #605 refused for a skipped leak assertion and
#760 refused for an unread directive: a run that asserted nothing must never report success.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_metadata import (  # noqa: E402
    collect_fixtures, parse_test_metadata, select_fixtures, should_run_runtime_test,
)


def _all() -> list[Path]:
    return collect_fixtures(TESTS_DIR)


def test_the_collector_found_a_corpus():
    """The always-fires control: a selector test over an empty corpus proves nothing."""
    assert len(_all()) > 1000, len(_all())


def test_an_unnarrowed_run_selects_every_fixture():
    assert set(select_fixtures(TESTS_DIR)) == set(_all())


def test_compile_only_selects_exactly_what_never_runs_a_binary():
    """`--compile-only` is a SELECTOR over the existing rule, not a second rule."""
    chosen = set(select_fixtures(TESTS_DIR, compile_only=True))
    expected = {f for f in _all()
                if not should_run_runtime_test(f, parse_test_metadata(f))}
    assert chosen == expected
    assert chosen, "no fixture avoids the binary; the rule cannot be right"


def test_compile_only_and_the_rest_partition_the_corpus():
    """Every fixture is on exactly one side, so neither flag can lose one."""
    compile_only = set(select_fixtures(TESTS_DIR, compile_only=True))
    runs = {f for f in _all() if should_run_runtime_test(f, parse_test_metadata(f))}
    assert compile_only | runs == set(_all())
    assert compile_only & runs == set()


def test_leaks_only_selects_the_leak_assertions():
    chosen = set(select_fixtures(TESTS_DIR, leaks_only=True))
    expected = {f for f in _all() if parse_test_metadata(f).expect_no_leaks}
    assert chosen == expected
    assert chosen, "no fixture carries a leak assertion; the rule cannot be right"


def test_the_flags_compose():
    """A leak assertion needs a run, so the two selectors together select nothing."""
    assert select_fixtures(TESTS_DIR, leaks_only=True, compile_only=True) == []


def test_a_filter_that_matches_nothing_selects_nothing():
    assert select_fixtures(TESTS_DIR, filter_pattern="zzz_no_such_area") == []


def test_a_filter_reads_the_path_under_tests():
    chosen = select_fixtures(TESTS_DIR, filter_pattern="diagnostics/")
    assert chosen, "the filter matched nothing at all"
    assert all("diagnostics/" in str(f.relative_to(TESTS_DIR)) for f in chosen)
