"""The stop-code census: the red fixtures of a run, sorted by what CE0155 names.

A fixture that fails with the stop code in its compiler output is counted under its
(stage, construct). The census reads the results of a run and decides nothing.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, List, Tuple

from sushi_lang.semantics.shir.stop import parse_stop


def count_stops(compilation_messages: Iterable[str]) -> Tuple[Counter, int]:
    """The stops by (stage, construct), and the number of failures with no stop."""
    stops: Counter = Counter()
    unstopped = 0
    for message in compilation_messages:
        stop = parse_stop(message)
        if stop is None:
            unstopped += 1
        else:
            stops[stop] += 1
    return stops, unstopped


def census_lines(compilation_messages: Iterable[str]) -> List[str]:
    """The table, highest count first, then stage and construct. Empty with no stop."""
    stops, unstopped = count_stops(compilation_messages)
    if not stops:
        return []
    rows = sorted(stops.items(), key=lambda row: (-row[1], row[0]))
    stage_width = max(len(stage) for (stage, _), _ in rows)
    lines = [f"Stop-code census ({sum(stops.values())} stopped, "
             f"{unstopped} failed with no stop code):"]
    lines += [f"  {count:>5}  {stage:<{stage_width}}  {construct}"
              for (stage, construct), count in rows]
    return lines
