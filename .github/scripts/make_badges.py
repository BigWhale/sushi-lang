#!/usr/bin/env python3
"""Render the README shields.io endpoint badges from this run's test output.

Stdlib only, deliberately: the badges job installs no toolchain, so this runs on the
runner's system python3.

Inputs are the two artifacts produced by the SAME workflow run that gated the commit:
  --corpus  tests/run_tests.py --enhanced --json   (the .sushi corpus)
  --pytest  pytest --junit-xml=...                 (the Python unit layer)

Reading this run's artifacts rather than re-running the suites is the point. The job this
replaces ran the compile-only runner by itself, so the badge described a suite nobody
gated on, and `|| true` plus `continue-on-error` plus a bare `json.load` meant any crash
left the previous green standing. Here, anything that cannot be computed is fatal and
nothing is published: a missing badge is honest, a stale green is not.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NoReturn

GREEN = "brightgreen"
RED = "red"
YELLOW = "yellow"

# Shields treats this as a floor. The gist raw CDN dominates real-world lag either way,
# but being explicit beats inheriting whatever the default becomes.
CACHE_SECONDS = 300


def _die(message: str) -> NoReturn:
    print(f"make_badges: {message}", file=sys.stderr)
    raise SystemExit(1)


@dataclass
class Suite:
    """One test layer's outcome, in the terms a badge needs."""

    label: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    # Green, but with a caveat that must not render as a clean run.
    degraded: str = ""
    # The producing job died without leaving numbers behind.
    unavailable: bool = False
    gist_file: str = field(default="")


def _load_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _die(f"cannot read {path}: {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # The runner prints stdlib and interposer build failures on stdout and exits before
        # emitting any JSON, so a failed build arrives here as prose. Quote it: the code
        # this replaces raised a bare JSONDecodeError with the actual cause invisible.
        _die(f"{path} is not JSON ({exc}); first 500 bytes:\n{text[:500]}")
    if not isinstance(data, dict):
        _die(f"{path}: expected a JSON object, got {type(data).__name__}")
    return data


def load_corpus(path: Path) -> Suite:
    """Parse `tests/run_tests.py --enhanced --json`."""
    data = _load_json(path)
    for key in ("total_tests", "passed", "failed"):
        if not isinstance(data.get(key), int):
            _die(f"{path}: missing or non-integer key {key!r}")

    # #241: a skipped leak assertion is not a passing one, which is why the runner's own
    # summary shouts about it in yellow. The badge says the same rather than reporting a
    # clean run over checks that silently did not happen.
    skipped_leaks = int(data.get("leak_checks_skipped", 0))

    return Suite(
        label="sushi tests",
        gist_file="badge_sushi.json",
        total=data["total_tests"],
        passed=data["passed"],
        failed=data["failed"],
        degraded=f"{skipped_leaks} leak checks skipped" if skipped_leaks else "",
    )


def load_pytest(path: Path) -> Suite:
    """Parse a pytest `--junit-xml` report.

    xunit2, the default since pytest 6, puts the counts on `<testsuite>` as attributes
    inside a `<testsuites>` root; the legacy family emits a bare `<testsuite>` root. Handle
    both rather than assuming. There is no `passed` attribute -- `tests` counts everything
    that ran, skips and errors included -- so passed is a subtraction.
    """
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        _die(f"{path}: {exc}")

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        _die(f"{path}: no <testsuite> element -- pytest did not finish writing the report")

    total = failures = errors = skipped = 0
    for suite in suites:
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))

    failed = failures + errors
    return Suite(
        label="python tests",
        gist_file="badge_python.json",
        total=total,
        passed=total - failed - skipped,
        failed=failed,
        skipped=skipped,
    )


def load_suite(path: Path, label: str, loader: Callable[[Path], Suite],
               job_result: str) -> Suite:
    """Read one suite's artifact, tolerating only the absence we can explain."""
    if not path.exists():
        if job_result == "success":
            _die(f"{path} is missing although its job reported success -- workflow bug")
        # The job failed or was skipped, so there is nothing to read. Report red rather
        # than crashing: crashing would abandon the publish and keep the last green.
        gist = "badge_sushi.json" if "sushi" in label else "badge_python.json"
        return Suite(label=label, gist_file=gist, unavailable=True)
    return loader(path)


def count_badge(suite: Suite) -> dict:
    """One suite's own badge, coloured by that suite alone."""
    if suite.unavailable:
        message, color = "unavailable", RED
    elif suite.failed:
        message, color = f"{suite.passed} passed, {suite.failed} failed", RED
    elif suite.degraded:
        message, color = f"{suite.total} passed ({suite.degraded})", YELLOW
    elif suite.skipped:
        message, color = f"{suite.passed} passed, {suite.skipped} skipped", GREEN
    else:
        message, color = f"{suite.total} passed", GREEN
    return _badge(suite.label, message, color)


def status_badge(suites: list[Suite], job_results: dict[str, str]) -> dict:
    """The roll-up: red if either layer fails, or if either job died with nothing to say.

    Order matters. A job that failed BECAUSE tests failed should read "failing", so that
    case is tested first. "errored" is reserved for a job that produced no numbers at all
    -- OOM, timeout, a toolchain install failure -- which is exactly the case the old job
    turned into an untouched gist and a stale green README.
    """
    if any(s.failed or s.unavailable for s in suites):
        message, color = "failing", RED
    elif any(result != "success" for result in job_results.values()):
        message, color = "errored", RED
    elif any(s.degraded for s in suites):
        message, color = "passing (degraded)", YELLOW
    else:
        message, color = "passing", GREEN
    return _badge("tests", message, color)


def _badge(label: str, message: str, color: str) -> dict:
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
        "cacheSeconds": CACHE_SECONDS,
    }


def build_badges(suites: list[Suite], job_results: dict[str, str]) -> dict[str, dict]:
    badges = {"badge_status.json": status_badge(suites, job_results)}
    for suite in suites:
        badges[suite.gist_file] = count_badge(suite)
    return badges


def build_payload(badges: dict[str, dict]) -> dict:
    return {
        "files": {
            name: {"content": json.dumps(badge, indent=2) + "\n"}
            for name, badge in badges.items()
        }
    }


def write_summary(path: Path, suites: list[Suite], status: dict) -> None:
    """Restore in the run summary what `--json` costs the PR log."""
    rows = "\n".join(
        f"| {s.label} | {s.total} | {s.passed} | {s.failed} | {s.skipped} |"
        for s in suites
    )
    block = (
        "## Test badges\n\n"
        "| suite | total | passed | failed | skipped |\n"
        "| --- | ---: | ---: | ---: | ---: |\n"
        f"{rows}\n\n"
        f"status: **{status['message']}**\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--corpus-job-result", default="success")
    parser.add_argument("--pytest", type=Path, required=True)
    parser.add_argument("--pytest-job-result", default="success")
    # test-macos uploads no artifact -- both suite files come from Linux -- so its job
    # result is the only signal it sends. Without it a macOS-only regression is invisible.
    parser.add_argument("--macos-job-result", default="success")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    suites = [
        load_suite(args.corpus, "sushi tests", load_corpus, args.corpus_job_result),
        load_suite(args.pytest, "python tests", load_pytest, args.pytest_job_result),
    ]
    job_results = {
        "test-linux": args.corpus_job_result,
        "pytest": args.pytest_job_result,
        "test-macos": args.macos_job_result,
    }
    badges = build_badges(suites, job_results)

    args.payload.write_text(json.dumps(build_payload(badges), indent=2), encoding="utf-8")
    if args.summary:
        write_summary(args.summary, suites, badges["badge_status.json"])

    for badge in badges.values():
        print(f"{badge['label']}: {badge['message']} ({badge['color']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
