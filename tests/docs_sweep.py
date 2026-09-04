"""Compile the Sushi in the documentation -- the pages, the doc blocks, and the files.

THREE collectors, one outcome vocabulary. This is a tool to run BY HAND, periodically
or after a docs edit, and it is deliberately not a CI job.

Usage: python tests/docs_sweep.py [--only {all,docs,examples,files}] [--jobs N] [--verbose]

## The pages (`--only docs`)

Every self-contained ```sushi block under `docs/` -- the #297 gate. Four outcomes,
because "compile everything, assert exit 0" would report a correct page as broken
(the issue's own analysis):

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

A page that TEACHES a doc block draws it inside a `~~~sushi` fence, so the illustration
holds a ```sushi fence of its own. CommonMark closes a fence only with a run of the SAME
character that is at least as long, so this collector steps over any fence it cannot
close -- a tilde run, or four or more backticks -- rather than reading inside it.

## The doc blocks (`--only examples`)

Every `- Example:` fence in a `.sushi` file under the scan roots. The collector PARSES,
so it sees exactly what the `docs` pass sees, and it knows whether the declaration is
public and whether the unit declares `main`. The attributes ride on the fence's own info
string, because a `.sushi` file cannot carry an HTML comment:

    ```sushi                        compile and run; a non-zero exit is a failure
    ```sushi no_run                 compile only -- it needs a file, a socket, a long loop
    ```sushi skip (reason)          do not compile; the reason is printed
    ```sushi error CExxxx           must exit 2 and name every code given
    ```text, ```python, ...         not a Sushi example; ignored

An example is compiled from OUTSIDE the unit it documents, the way a rustdoc doctest
links its crate: one generated entry file that imports the unit and holds the snippet.
Two things are then out of reach, and each is a printed SKIP rather than a failure -- a
private declaration, which the generated file cannot call, and a unit that declares
`main`, which cannot be imported beside a second one.

Output is not asserted. An example is documentation, and an expected-output mechanism
would make it a test. `docs/design/documentation.md` section 10 is the authority.

## The files (`--only files`)

Every `.sushi` FILE under `docs/` -- 109 of them, and the tutorial's `--8<--` includes
among them (#547). Neither collector above compiles a file: one reads fences out of
Markdown, the other compiles fences OUT OF a file. Three files were broken for two
phases of the handles epic before anybody noticed.

The file is compiled, never run: what a page promises about its OUTPUT is the reader's
to check, and running an example that opens a socket is not this tool's business.

Two rules keep the corpus honest without a marker on a single file:

- a file with no `fn main(` at the start of a line is a LIBRARY, and is built as one
  (`--lib --lib-version 0.0.0`). "No main" is then a category, never drift, and the
  library is genuinely gated instead of skipped
- every library of a directory is built BEFORE that directory's programs, into a
  temporary directory the programs get on `SUSHI_LIB_PATH`. So the tutorial's
  `use <lib/guidelib>` page is checked end to end, against the library it ships

The directive is the escape hatch for what neither rule covers. A `.sushi` file cannot
carry an HTML comment, so it carries the same words in a comment of its own, and only
in the LEADING comment block -- the attachment rule a doc block already obeys:

    # docs-sweep: skip (reason)      do not compile; the reason is printed
    # docs-sweep: error CExxxx       must exit 2 and name every code given

Nothing is written beside the file: the output and the incremental cache both go to a
temporary directory (`--cache-dir`).

This collector also checks every page's `--8<--` snippet include, and FAILS one whose
target is not on disk. A page that includes a file nobody compiled was the reported
hole; a page that includes a file that is not there ships a broken snippet. Each
include is counted, so the summary says the check ran.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FENCE_OPEN = re.compile(r"^```sushi\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")
# A fence this collector cannot close, so it must be stepped over whole.
FOREIGN_FENCE = re.compile(r"^(`{4,}|~{3,})")
SKIP_MARK = re.compile(r"<!--\s*docs-sweep:\s*skip\s*(?:\((?P<reason>[^)]*)\))?\s*-->")
ERROR_MARK = re.compile(r"<!--\s*docs-sweep:\s*error\s+(?P<codes>CE\d{4}(?:\s+CE\d{4})*)\s*-->")

# The same roots `tests/unit/test_doc_block_grammar.py` names, less `site/`: that one is
# the built copy of `docs/`, and sweeping a build output would report every page twice.
SUSHI_ROOTS = ("docs", "editor-support", "sushi_lang", "tests", "toolchain")
SKIP_DIRS = {"__sushi_cache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}

# The FILE directive (#547): a `.sushi` file cannot carry an HTML comment, and a
# comment of its own is what it has. The same words as a fence, in `#` instead of `<!--`.
FILE_MARK = re.compile(r"^#\s*docs-sweep:\s*(?P<attr>.+?)\s*$")
# A `main` DECLARATION, at the start of a line: half the corpus names `fn main()` in
# its prose, and a file that only talks about one is a library.
MAIN_DECL = re.compile(r"^fn\s+main\s*\(", re.M)
# A mkdocs snippet include, the way the tutorial pulls its examples in.
INCLUDE_MARK = re.compile(r'--8<--\s*"(?P<target>[^"]+)"')

SKIP_ATTR = re.compile(r"^skip\s*(?:\((?P<reason>[^)]*)\))?$")
ERROR_ATTR = re.compile(r"^error\s+(?P<codes>CE\d{4}(?:\s+CE\d{4})*)$")
USE_LINE = re.compile(r"^use\s")

COMPILE_TIMEOUT = 60
RUN_TIMEOUT = 10

OUTCOMES = ("PASS", "EXPECTED-ERROR", "SKIP", "FAIL")


@dataclass
class Block:
    page: str
    line: int          # 1-based line of the opening fence
    source: str
    skip_reason: str | None
    expected_codes: list[str]


@dataclass
class Attrs:
    """What a fence's info string asks the runner to do."""
    mode: str                              # run | no_run | skip | error | ignore | unknown
    reason: str = ""
    codes: tuple[str, ...] = ()
    written: str = ""                      # the info string, for the FAIL detail


@dataclass
class Example:
    file: str            # repo-relative path of the `.sushi` file
    line: int            # 1-based line of the opening fence
    code: str
    attrs: Attrs
    unit_path: Path      # the documented unit, absolute
    index: int           # a per-file counter; it names the generated helper
    owner: str           # the documented declaration, "" for a unit block
    skip_reason: str | None = None


# -- the Markdown pages ---------------------------------------------------------

def blocks_in(page: str, text: str) -> list[Block]:
    """Every candidate block on one page."""
    from sushi_lang.semantics.ast_builder.declarations.docs import closes_fence

    lines = text.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        foreign = FOREIGN_FENCE.match(lines[i])
        if foreign is not None:
            # An illustration of a doc block holds a ```sushi fence that belongs to the
            # illustration and not to the page. Step to this fence's own closer.
            marker = foreign.group(1)
            i += 1
            while i < len(lines) and not closes_fence(marker, lines[i]):
                i += 1
            i += 1
            continue

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


def collect_blocks() -> list[Block]:
    pages = sorted(str(p.relative_to(PROJECT_ROOT))
                   for p in (PROJECT_ROOT / "docs").rglob("*.md"))
    blocks: list[Block] = []
    for page in pages:
        blocks.extend(blocks_in(page, (PROJECT_ROOT / page).read_text(errors="replace")))
    return blocks


def compile_block(block: Block, tmpdir: str) -> tuple[Block, str, str]:
    """Returns (block, outcome, detail)."""
    if block.skip_reason is not None:
        return block, "SKIP", block.skip_reason

    stem = f"{Path(block.page).stem}_{block.line}"
    src = Path(tmpdir) / f"{stem}.sushi"
    src.write_text(block.source)
    out = Path(tmpdir) / stem
    try:
        proc = _sushic(src, out)
    except subprocess.TimeoutExpired:
        return block, "FAIL", f"compile timed out after {COMPILE_TIMEOUT}s"

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


# -- the doc blocks -------------------------------------------------------------

def read_attr(rest: str, written: str) -> Attrs:
    """The attribute vocabulary itself, shared by every collector that carries one.

    A fence spells it in its info string and a FILE spells it in a `docs-sweep:`
    comment, but the words mean one thing, so they are read in one place.
    """
    if not rest:
        return Attrs("run", written=written)
    if rest == "no_run":
        return Attrs("no_run", written=written)
    skip = SKIP_ATTR.match(rest)
    if skip is not None:
        return Attrs("skip", reason=skip.group("reason") or "no reason given",
                     written=written)
    error = ERROR_ATTR.match(rest)
    if error is not None:
        return Attrs("error", codes=tuple(error.group("codes").split()), written=written)
    # A typo of `no_run` must be visible. An unrecognised attribute is its own outcome
    # rather than a silent skip, which is the failure mode this whole feature removes.
    return Attrs("unknown", written=written)


def parse_attrs(info: str) -> Attrs:
    """One fence info string, read into what the runner must do with the fence."""
    words = info.split(None, 1)
    if not words or words[0] != "sushi":
        return Attrs("ignore", written=info)
    return read_attr(words[1].strip() if len(words) > 1 else "", info)


def wrap_example(code: str, unit_import: str, index: int) -> str:
    """One example as a whole program (documentation.md S10, R19 and R20).

    A snippet with no `fn main(` goes into a helper, and `main` matches on the result.
    A bare `main` holding a `??` warns CW2511 on every such example, and that warning
    exists to discourage `??` in `main`: a harness that writes the discouraged form on
    the author's behalf teaches it.
    """
    uses: list[str] = []
    body: list[str] = []
    for line in code.split("\n"):
        # A `use` inside a function body does not parse, so every one is hoisted, in
        # the order written.
        (uses if USE_LINE.match(line) else body).append(line)
    if unit_import and unit_import not in uses:
        uses.insert(0, unit_import)          # a duplicate `use` is CW3001

    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()

    header = "\n".join(uses) + "\n\n" if uses else ""
    if "fn main(" in code:
        return header + "\n".join(body) + "\n"

    name = f"doc_example_{index}"            # the index cannot collide with a symbol
    indented = "\n".join(f"    {line}" if line.strip() else "" for line in body)
    return (
        f"{header}"
        f"fn {name}() ~:\n"
        f"{indented}\n"
        f"    return Result.Ok(~)\n"
        f"\n"
        f"fn main() i32:\n"
        f"    match {name}():\n"
        f"        Result.Ok(_) ->\n"
        f"            return Result.Ok(0)\n"
        f"        Result.Err(_) ->\n"
        f"            return Result.Ok(1)\n"
    )


def _read_unit(path: Path):
    """The parsed unit, or None when the file does not parse or holds no block."""
    from sushi_lang.internals.parser import parse_to_ast

    text = path.read_text(encoding="utf-8", errors="replace")
    if "##:" not in text:
        return None
    try:
        program, _tree = parse_to_ast(text, dump_parse=False)
    except Exception:
        return None
    return program


def _unreachable(owner, declares_main: bool, path: Path) -> str | None:
    """Why an example cannot be compiled from outside its unit (R21), or None.

    Both facts come out of the parse. A skip is printed and counted, so the hole in the
    coverage stays visible rather than looking like a pass.
    """
    if declares_main:
        return f"'{path.name}' declares main, so no generated file can import it"
    fields = getattr(type(owner), "__dataclass_fields__", {})
    if "is_public" in fields and not owner.is_public:
        name = getattr(owner, "name", "this declaration")
        return f"'{name}' is private, so an example cannot call it from outside"
    return None


def _examples_of(path: Path, program) -> list[Example]:
    from sushi_lang.semantics.passes.docs import documented

    declares_main = any(func.name == "main" for func in program.functions)
    found: list[Example] = []
    index = 0
    for doc, owner in documented(program):
        for example in doc.examples:
            index += 1
            if example.defect is not None:
                continue          # CE7007 and CE7008 are the compiler's business
            attrs = parse_attrs(example.attrs)
            if attrs.mode == "ignore":
                continue
            found.append(Example(
                file=str(path.relative_to(PROJECT_ROOT))
                if path.is_relative_to(PROJECT_ROOT) else str(path),
                line=example.loc.line if example.loc is not None else 0,
                code=example.code,
                attrs=attrs,
                unit_path=path,
                index=index,
                owner=getattr(owner, "name", "") if owner is not None else "",
                skip_reason=_unreachable(owner, declares_main, path),
            ))
    return found


def examples_in(path: Path) -> list[Example]:
    """Every runnable example in one `.sushi` file."""
    program = _read_unit(path)
    return [] if program is None else _examples_of(path, program)


def sushi_files() -> list[Path]:
    found: list[Path] = []
    for root in SUSHI_ROOTS:
        for path in sorted((PROJECT_ROOT / root).rglob("*.sushi")):
            if not any(part in SKIP_DIRS for part in path.parts):
                found.append(path)
    return found


def collect_examples(files: list[Path] | None = None) -> tuple[list[Example], list[str]]:
    """Every example under the scan roots, and the files that did not parse."""
    examples: list[Example] = []
    unparsed: list[str] = []
    for path in files if files is not None else sushi_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "##:" not in text:
            continue
        program = _read_unit(path)
        if program is None:
            unparsed.append(str(path.relative_to(PROJECT_ROOT)))
            continue
        examples.extend(_examples_of(path, program))
    return examples, unparsed


_COPIES: dict[tuple[str, str], Path] = {}
_COPY_LOCK = threading.Lock()


def _unit_copy(unit_dir: Path, tmproot: Path) -> Path:
    """A temp copy of the unit's own directory, made once per directory (R18).

    A unit import resolves against the ENTRY file's directory and there is no search
    path, so a `use "helpers/x"` inside the documented unit resolves only from beside
    that unit. The generated file has to stand there, and it must not be written into
    the source tree.
    """
    key = (str(tmproot), str(unit_dir))
    with _COPY_LOCK:
        made = _COPIES.get(key)
        if made is not None and made.exists():
            return made
        dest = tmproot / "units" / f"{len(_COPIES)}_{unit_dir.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(unit_dir, dest,
                        ignore=shutil.ignore_patterns("__sushi_cache__", "bin"))
        _COPIES[key] = dest
        return dest


def run_example(example: Example, tmproot: Path) -> tuple[Example, str, str]:
    """Returns (example, outcome, detail)."""
    if example.skip_reason is not None:
        return example, "SKIP", example.skip_reason
    if example.attrs.mode == "skip":
        return example, "SKIP", example.attrs.reason
    if example.attrs.mode == "unknown":
        return example, "FAIL", f"unknown fence attribute: `{example.attrs.written}`"

    tmproot = Path(tmproot)
    unit_dir = _unit_copy(example.unit_path.parent, tmproot)
    stem = f"doc_example_{example.unit_path.stem}_{example.index}"
    entry = unit_dir / f"{stem}.sushi"
    entry.write_text(wrap_example(example.code, f'use "{example.unit_path.stem}"',
                                  example.index), encoding="utf-8")

    # Its own working directory, so an example that writes a file leaves nothing behind.
    work = tmproot / "runs" / f"{unit_dir.name}_{stem}"
    work.mkdir(parents=True, exist_ok=True)
    binary = work / stem

    try:
        proc = _sushic(entry, binary)
    except subprocess.TimeoutExpired:
        return example, "FAIL", f"compile timed out after {COMPILE_TIMEOUT}s"

    if example.attrs.codes:
        missing = [code for code in example.attrs.codes if code not in proc.stderr]
        if proc.returncode == 2 and not missing:
            return example, "EXPECTED-ERROR", ", ".join(example.attrs.codes)
        return example, "FAIL", (
            f"exit {proc.returncode}, missing {missing or list(example.attrs.codes)}: "
            f"{first_error(proc.stderr)}")

    if proc.returncode not in (0, 1):  # exit 1 is a warning, not a failure
        return example, "FAIL", f"exit {proc.returncode}: {first_error(proc.stderr)}"
    if example.attrs.mode == "no_run":
        return example, "PASS", "compiled, not run"

    try:
        run = subprocess.run([str(binary)], capture_output=True, text=True,
                             cwd=work, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return example, "FAIL", f"run timed out after {RUN_TIMEOUT}s"
    if run.returncode != 0:
        return example, "FAIL", f"ran, exit {run.returncode}: {run.stderr.strip()}"
    return example, "PASS", ""


# -- the files (`--only files`) -------------------------------------------------

@dataclass
class DocFile:
    path: Path           # absolute
    file: str            # repo-relative where it can be made, else the name
    attrs: Attrs
    is_library: bool


def parse_file_attrs(text: str) -> Attrs:
    """The `# docs-sweep:` directive of a file, read from its LEADING comment block.

    The attachment rule doc blocks already obey: the block is the run of comment
    lines at the top of the file, and the first line that is not one ends it. A
    directive further down is a note to a reader, not an instruction to this tool.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        mark = FILE_MARK.match(stripped)
        if mark is not None:
            return read_attr(mark.group("attr").strip(), stripped)
    return Attrs("run", written="")


def doc_file_at(path: Path) -> DocFile:
    """One `.sushi` file, read into what the sweep must do with it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        name = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        name = path.name
    # No `main` is a CATEGORY and never drift: the file is a library, and it is built
    # as one. The declaration has to be at the start of a line -- half the corpus
    # names `fn main()` in its prose.
    return DocFile(path=path, file=name, attrs=parse_file_attrs(text),
                   is_library=MAIN_DECL.search(text) is None)


def snippet_includes() -> list[tuple[str, int, str, bool]]:
    """Every `--8<--` snippet on every page: where it is, what it names, and whether
    that target is on disk.

    The other half of #547: a page that includes a file nobody compiled was the
    reported hole, and a page that includes a file that is not THERE ships a broken
    snippet to the reader. Both are answered by the corpus of files, so both are here.
    Each include is COUNTED and not only reported on failure -- a check that is silent
    when it passes is invisible, which is the failure mode this whole tool removes.
    """
    found: list[tuple[str, int, str, bool]] = []
    for page in sorted((PROJECT_ROOT / "docs").rglob("*.md")):
        text = page.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            mark = INCLUDE_MARK.search(line)
            if mark is None:
                continue
            target = mark.group("target")
            found.append((str(page.relative_to(PROJECT_ROOT)), number, target,
                          (PROJECT_ROOT / target).exists()))
    return found


def missing_includes() -> list[tuple[str, int, str]]:
    """Only the includes whose target is not on disk."""
    return [(page, number, target)
            for page, number, target, exists in snippet_includes() if not exists]


def doc_files() -> list[DocFile]:
    """Every `.sushi` file under `docs/` (#547)."""
    found: list[DocFile] = []
    for path in sorted((PROJECT_ROOT / "docs").rglob("*.sushi")):
        if not any(part in SKIP_DIRS for part in path.parts):
            found.append(doc_file_at(path))
    return found


def _lib_dir(tmproot: Path, owner: Path, made: dict[Path, Path]) -> Path:
    """Where the libraries of one directory are built, one directory per source dir.

    Per directory and not one shared pile, so two libraries of one name -- a thing a
    documentation corpus is free to hold -- cannot shadow each other.
    """
    dest = made.get(owner)
    if dest is None:
        dest = tmproot / "libs" / f"{len(made)}_{owner.name}"
        dest.mkdir(parents=True, exist_ok=True)
        made[owner] = dest
    return dest


def compile_doc_file(doc: DocFile, tmproot: Path, lib_dir: Path) -> tuple[DocFile, str, str]:
    """Returns (doc, outcome, detail). A library is built with `--lib` into lib_dir."""
    if doc.attrs.mode == "skip":
        return doc, "SKIP", doc.attrs.reason
    if doc.attrs.mode == "unknown":
        return doc, "FAIL", f"unknown docs-sweep attribute: `{doc.attrs.written}`"

    if doc.is_library:
        out = lib_dir / f"{doc.path.stem}.slib"
        extra: tuple[str, ...] = ("--lib", "--lib-version", "0.0.0")
    else:
        out = tmproot / "bin" / f"{doc.path.stem}_{abs(hash(doc.file)) % 10**6}"
        out.parent.mkdir(parents=True, exist_ok=True)
        extra = ()
    # Its own cache, because the collector writes nothing beside the file it reads.
    extra = (*extra, "--cache-dir", str(tmproot / "cache"))

    try:
        proc = _sushic(doc.path, out, extra=extra, lib_path=lib_dir)
    except subprocess.TimeoutExpired:
        return doc, "FAIL", f"compile timed out after {COMPILE_TIMEOUT}s"

    if doc.attrs.codes:
        missing = [code for code in doc.attrs.codes if code not in proc.stderr]
        if proc.returncode == 2 and not missing:
            return doc, "EXPECTED-ERROR", ", ".join(doc.attrs.codes)
        return doc, "FAIL", (f"exit {proc.returncode}, missing "
                             f"{missing or list(doc.attrs.codes)}: {first_error(proc.stderr)}")

    if proc.returncode not in (0, 1):  # exit 1 is a warning, not a failure
        return doc, "FAIL", f"exit {proc.returncode}: {first_error(proc.stderr)}"
    return doc, "PASS", "built as a library" if doc.is_library else ""


def run_doc_files(docs: list[DocFile], tmproot: Path,
                  jobs: int = 1) -> list[tuple[DocFile, str, str]]:
    """Every library of a directory before that directory's programs.

    The tutorial teaches `use <lib/guidelib>` against a library it also ships, so the
    story is only checked end to end if the `.slib` exists when the consumer compiles.
    The libraries go where `SUSHI_LIB_PATH` points the consumer.
    """
    tmproot = Path(tmproot)
    tmproot.mkdir(parents=True, exist_ok=True)
    made: dict[Path, Path] = {}
    dirs = {doc.path.parent: _lib_dir(tmproot, doc.path.parent, made) for doc in docs}

    results: list[tuple[DocFile, str, str]] = []
    for doc in (d for d in docs if d.is_library):
        results.append(compile_doc_file(doc, tmproot, dirs[doc.path.parent]))

    programs = [d for d in docs if not d.is_library]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results.extend(pool.map(
            lambda d: compile_doc_file(d, tmproot, dirs[d.path.parent]), programs))
    return results


# -- the driver -----------------------------------------------------------------

def _sushic(src: Path, out: Path, *, extra: tuple[str, ...] = (),
            lib_path: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NO_COLOR": "1"}
    if lib_path is not None:
        env["SUSHI_LIB_PATH"] = str(lib_path)
    return subprocess.run(
        ["./sushic", *extra, "-o", str(out), str(src)],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
        env=env, timeout=COMPILE_TIMEOUT,
    )


def first_error(stderr: str) -> str:
    for line in stderr.splitlines():
        if "error [" in line:
            return line.strip()
    return stderr.strip().splitlines()[0] if stderr.strip() else "(no stderr)"


@dataclass
class Tally:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    failures: list[str] = field(default_factory=list)

    def record(self, outcome: str, where: str, detail: str, verbose: bool) -> None:
        self.counts[outcome] += 1
        if outcome == "FAIL":
            self.failures.append(f"{where}  {detail}")
        if verbose or outcome == "FAIL":
            print(f"[{outcome}] {where} {detail}")

    def line(self, noun: str) -> str:
        return (f"{sum(self.counts.values())} {noun}: {self.counts['PASS']} pass, "
                f"{self.counts['EXPECTED-ERROR']} expected-error, "
                f"{self.counts['SKIP']} skipped, {self.counts['FAIL']} FAILED")


def sweep_pages(tmpdir: str, jobs: int, verbose: bool) -> Tally:
    tally = Tally()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for block, outcome, detail in pool.map(
                lambda b: compile_block(b, tmpdir), collect_blocks()):
            tally.record(outcome, f"{block.page}:{block.line}", detail, verbose)
    return tally


def sweep_examples(tmpdir: str, jobs: int, verbose: bool) -> Tally:
    tally = Tally()
    examples, unparsed = collect_examples()
    if unparsed:
        print(f"{len(unparsed)} file(s) did not parse and hold no example: "
              f"{', '.join(unparsed[:3])}{' ...' if len(unparsed) > 3 else ''}")
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for example, outcome, detail in pool.map(
                lambda e: run_example(e, Path(tmpdir)), examples):
            where = f"{example.file}:{example.line} {example.owner}".rstrip()
            tally.record(outcome, where, detail, verbose)
    return tally


def sweep_files(tmpdir: str, jobs: int, verbose: bool) -> Tally:
    tally = Tally()
    for page, number, target, exists in snippet_includes():
        detail = f"--8<-- `{target}`" + ("" if exists else ", which is not on disk")
        tally.record("PASS" if exists else "FAIL", f"{page}:{number}", detail, verbose)
    for doc, outcome, detail in run_doc_files(doc_files(), Path(tmpdir) / "files", jobs):
        tally.record(outcome, doc.file, detail, verbose)
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("all", "docs", "examples", "files"),
                        default="all", help="which collector to run (default: all three)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--verbose", action="store_true",
                        help="list every block, not only the failures")
    args = parser.parse_args()

    tallies: list[tuple[str, Tally]] = []
    with tempfile.TemporaryDirectory(prefix="docs_sweep_") as tmpdir:
        if args.only in ("all", "docs"):
            tallies.append(("candidate blocks",
                            sweep_pages(tmpdir, args.jobs, args.verbose)))
        if args.only in ("all", "examples"):
            tallies.append(("doc examples",
                            sweep_examples(tmpdir, args.jobs, args.verbose)))
        if args.only in ("all", "files"):
            tallies.append(("files and snippet includes under docs/",
                            sweep_files(tmpdir, args.jobs, args.verbose)))

    print()
    for noun, tally in tallies:
        print(tally.line(noun))

    failures = [line for _noun, tally in tallies for line in tally.failures]
    if failures:
        print("\nFailing (documentation drift). On a page, mark the line above the "
              "fence: `<!-- docs-sweep: error CExxxx -->` for a deliberate diagnostic, "
              "`<!-- docs-sweep: skip (reason) -->` to exclude. In a doc block, put "
              "the same words on the fence itself: ```sushi error CExxxx, "
              "```sushi skip (reason), or ```sushi no_run to compile without running. "
              "In a `.sushi` FILE, the same words in a comment of its leading block: "
              "`# docs-sweep: error CExxxx`, `# docs-sweep: skip (reason)`:")
        for line in failures:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
