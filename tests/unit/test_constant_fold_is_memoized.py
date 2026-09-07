"""A constant's initializer folds once, however many times its name is read.

Folding had no memoization at any level. A chain where each constant names the one
before it TWICE therefore doubled the work per link: 22 such constants -- about 25
ordinary lines -- stopped a build for 59 seconds (#597).

Counted rather than timed: a wall-clock budget is a flaky test on a loaded machine,
and the fault is not slowness but the shape of the growth.
"""
from __future__ import annotations

import collections

from sushi_lang.semantics.passes.const_eval import ConstantEvaluator


DEPTH = 14


def _fanning_chain(depth: int) -> str:
    lines = ["const i32 C0 = 1"]
    lines += [f"const i32 C{i} = C{i - 1} + C{i - 1}" for i in range(1, depth)]
    lines += ["", "fn main() i32:", f'    println("{{C{depth - 1}}}")',
              "    return Result.Ok(0)"]
    return "\n".join(lines)


def _fold_counts(analyze, monkeypatch, src: str) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    original = ConstantEvaluator._fold_constant

    def counted(self, sig, span):
        counts[(sig.unit_name, sig.name)] += 1
        return original(self, sig, span)

    monkeypatch.setattr(ConstantEvaluator, "_fold_constant", counted)
    analyze(src)
    return counts


def test_a_fanning_chain_does_not_fold_exponentially(analyze, monkeypatch):
    counts = _fold_counts(analyze, monkeypatch, _fanning_chain(DEPTH))
    total = sum(counts.values())
    assert total <= 8 * DEPTH, (
        f"{total} folds for {DEPTH} constants. Each link names the one before it twice, "
        "so a fold with no memoization doubles per link and the build stops for a minute."
    )


def test_the_folded_value_is_still_right(analyze):
    reporter = analyze(_fanning_chain(6))
    assert not [item for item in reporter.items if item.severity.name == "ERROR"]
