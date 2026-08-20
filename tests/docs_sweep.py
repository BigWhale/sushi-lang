"""Compile every self-contained ```sushi block under docs/ -- the #297 gate.

Four outcomes per block, because "compile everything, assert exit 0" would report a
correct page as broken (the issue's own analysis):

- PASS            the block compiles (exit 0, or exit 1 -- a warning is not a failure;
                  counting warnings turns 34 fails into 96, the trap BUGS.md pins)
- EXPECTED-ERROR  the fence is marked `<!-- docs-sweep: error CExxxx -->` (several codes
                  space-separated), so the block demonstrates a diagnostic on purpose:
                  it must exit 2 AND stderr must name every marked code
- SKIP            the fence is marked `<!-- docs-sweep: skip (reason) -->`: the block is
                  not self-contained (it calls helpers the narrative defined earlier) or
                  needs a `.slib` built first
- FAIL            everything else -- documentation drift

Both markers sit on the line ABOVE the opening fence. The expected-error marker is
fence-level, NOT the inline `# ERROR CExxxx:` comment convention -- the inline form is
ambiguous by usage: most of the corpus writes it as an annotation on a COMMENTED-OUT
line, in a block that must compile.

Only blocks containing `fn main(` AND a `return` are candidates: a fragment without a
main is prose, and so is a lone `fn main() i32:` line quoted to explain the signature
(a legal main always returns, so a mainful block with no return cannot be a program).
Usage: python tests/docs_sweep.py [--jobs N] [--verbose]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FENCE_OPEN = re.compile(r"^```sushi\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")
SKIP_MARK = re.compile(r"<!--\s*docs-sweep:\s*skip\s*(?:\((?P<reason>[^)]*)\))?\s*-->")
ERROR_MARK = re.compile(r"<!--\s*docs-sweep:\s*error\s+(?P<codes>CE\d{4}(?:\s+CE\d{4})*)\s*-->")


@dataclass
class Block:
    page: str
    line: int          # 1-based line of the opening fence
    source: str
    skip_reason: str | None
    expected_codes: list[str]


def collect_blocks() -> list[Block]:
    tracked = subprocess.run(
        ["git", "ls-files", "docs/**/*.md", "docs/*.md"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, check=True,
    ).stdout.splitlines()

    blocks: list[Block] = []
    for page in tracked:
        lines = (PROJECT_ROOT / page).read_text(errors="replace").splitlines()
        i = 0
        while i < len(lines):
            if not FENCE_OPEN.match(lines[i]):
                i += 1
                continue
            start = i
            i += 1
            body: list[str] = []
            while i < len(lines) and not FENCE_CLOSE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # past the closing fence
            source = "\n".join(body)
            if "fn main(" not in source or "return" not in source:
                continue
            marker_line = lines[start - 1] if start > 0 else ""
            skip = SKIP_MARK.search(marker_line)
            err = ERROR_MARK.search(marker_line)
            blocks.append(Block(
                page=page,
                line=start + 1,
                source=source + "\n",
                skip_reason=(skip.group("reason") or "no reason given") if skip else None,
                expected_codes=err.group("codes").split() if err else [],
            ))
    return blocks


def compile_block(block: Block, tmpdir: str) -> tuple[Block, str, str]:
    """Returns (block, outcome, detail)."""
    if block.skip_reason is not None:
        return block, "SKIP", block.skip_reason

    stem = f"{Path(block.page).stem}_{block.line}"
    src = Path(tmpdir) / f"{stem}.sushi"
    src.write_text(block.source)
    out = Path(tmpdir) / stem
    proc = subprocess.run(
        ["./sushic", "-o", str(out), str(src)],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
        env={**os.environ, "NO_COLOR": "1"}, timeout=60,
    )

    if block.expected_codes:
        missing = [c for c in block.expected_codes if c not in proc.stderr]
        if proc.returncode == 2 and not missing:
            return block, "EXPECTED-ERROR", ", ".join(block.expected_codes)
        detail = (f"exit {proc.returncode}, missing {missing or block.expected_codes}: "
                  f"{first_error(proc.stderr)}")
        return block, "FAIL", detail

    if proc.returncode in (0, 1):  # exit 1 is a warning, not a failure
        return block, "PASS", ""
    return block, "FAIL", f"exit {proc.returncode}: {first_error(proc.stderr)}"


def first_error(stderr: str) -> str:
    for line in stderr.splitlines():
        if "error [" in line:
            return line.strip()
    return stderr.strip().splitlines()[0] if stderr.strip() else "(no stderr)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--verbose", action="store_true",
                        help="list every block, not only the failures")
    args = parser.parse_args()

    blocks = collect_blocks()
    counts = {"PASS": 0, "EXPECTED-ERROR": 0, "SKIP": 0, "FAIL": 0}
    failures: list[tuple[Block, str]] = []

    with tempfile.TemporaryDirectory(prefix="docs_sweep_") as tmpdir:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = pool.map(lambda b: compile_block(b, tmpdir), blocks)
            for block, outcome, detail in results:
                counts[outcome] += 1
                if outcome == "FAIL":
                    failures.append((block, detail))
                if args.verbose or outcome == "FAIL":
                    print(f"[{outcome}] {block.page}:{block.line} {detail}")

    total = sum(counts.values())
    print(f"\n{total} candidate blocks: {counts['PASS']} pass, "
          f"{counts['EXPECTED-ERROR']} expected-error, {counts['SKIP']} skipped, "
          f"{counts['FAIL']} FAILED")
    if failures:
        print("\nFailing blocks (documentation drift -- fix the block, or mark the "
              "line above the fence: `<!-- docs-sweep: error CExxxx -->` for a "
              "deliberate diagnostic, `<!-- docs-sweep: skip (reason) -->` to exclude):")
        for block, detail in failures:
            print(f"  {block.page}:{block.line}  {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
