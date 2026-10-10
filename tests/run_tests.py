#!/usr/bin/env python3
"""Comprehensive test runner for Sushi language compiler."""

import argparse
import json
import re
import subprocess
import sys
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sushi_lang.internals.report import SPELLING_GATE_ENV  # noqa: E402
from test_metadata import corpus_files  # noqa: E402


DEFAULT_JOBS = 4
JOBS_ENV_VAR = "SUSHI_TEST_JOBS"


@lru_cache(maxsize=1)
def default_jobs() -> int:
    """The parallel-job count: SUSHI_TEST_JOBS, or DEFAULT_JOBS when it is unset.

    Read once per process. Both parsers evaluate this default, and run_tests imports
    the enhanced runner rather than spawning it, so without the cache a rejected
    value announced itself twice.

    ONE reader, for every entry point. The default used to be a literal in four
    places, one of them a `!= 4` comparison on the hop from here to the enhanced
    runner -- so moving the number would have quietly stopped an explicit `-j 4`
    from being forwarded at all.

    A GitHub standard runner has 4 vCPU on Linux and 3 on macOS, so 4 is the number
    that suits CI. A development machine with more cores exports a bigger one.
    """
    raw = os.environ.get(JOBS_ENV_VAR)
    if raw is None:
        return DEFAULT_JOBS

    try:
        jobs = int(raw)
    except ValueError:
        jobs = 0

    if jobs < 1:
        # stderr: this runs before --json is read, and --json stdout is a report
        # the badge job parses.
        print(f"Ignoring {JOBS_ENV_VAR}={raw!r}: expected a positive integer. "
              f"Using {DEFAULT_JOBS}.", file=sys.stderr)
        return DEFAULT_JOBS

    return jobs


def build_stdlib(project_root: Path, verbose: bool = False) -> bool:
    """Build the standard library. Returns True on success."""
    if verbose:
        print("Building standard library...")

    try:
        result = subprocess.run(
            [sys.executable, str(project_root / "sushi_lang" / "sushi_stdlib" / "build.py")],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            print(f"Failed to build stdlib: {result.stderr}")
            return False
        if verbose:
            print(result.stdout)
        return True
    except subprocess.TimeoutExpired:
        print("Stdlib build timed out")
        return False
    except Exception as e:
        print(f"Error building stdlib: {e}")
        return False


# Helper libraries that must ship as BINARY. Source is the default everywhere else, so
# most of tests/libs/ exercises the source path end to end. These do not, because their
# tests assert binary-path behaviour that source distribution deliberately removes:
# private_closure_lib backs test_err_lib_private_clash, which expects CE5007 -- a clash
# with an export-closure private. A source library has no export closure and namespaced
# units, so that clash cannot happen (docs/design/libraries.md section 4.2).
# kept_private_lib backs tests/libs/kept_private, which asserts the wording the BINARY
# path gives a private the closure never shipped (#469). A source library ships its
# units, so the name resolves there and a different code path answers.
# mangle_closure_lib backs tests/namespaces/mangling/test_lib_link_symbol, which asserts
# that a call into shipped bitcode uses the PRODUCER's symbol. A source library
# recompiles and derives its own, so the field under test is never read.
# twolib_bin backs tests/libs/multi_unit, the #494 shape: two units, each with a
# private `helper`, shipped as one binary manifest. twolib_src is its source twin
# and takes the default.
# binary_api_lib backs tests/libs/binary_api, which pins the manifest arms the
# `libraries` step reads at a consumer: a public struct and enum from the registry, a
# concrete perk implementation, a constrained generic, a generic enum template and a
# parameter pack. A source library reaches none of that code (#675).
# private_generic_type_lib backs test_lib_binary_private_generic_type: a private generic
# struct the closure ships under two manifest arms, which only the binary path reads.
# generic_ext_bin_lib backs tests/libs/binary_generic_extension, which pins a CONSUMER's
# own extension on a library's generic type. A source library's units are collected
# beside the consumer's, so the base is in the tables either way and the seed under test
# is never read (#728).
# fs_user_bin_lib backs tests/libs/binary_stdlib_import: a unit that imports <io/fs>. A
# source library ships its text and no manifest index, so the fault it pins (the
# injected stdlib unit shipped as the library's own API, CE4001 at the consumer) is a
# binary-path fault (#985).
BINARY_ONLY_HELPERS = {"private_closure_lib", "kept_private_lib", "const_lib", "var_bin_lib",
                       "mangle_closure_lib", "twolib_bin", "channel_bin_lib",
                       "generic_perk_bin_lib", "binary_api_lib", "private_generic_type_lib",
                       "generic_ext_bin_lib", "fs_user_bin_lib"}


def helper_sources(helpers_dir: Path) -> List[Path]:
    """The entry point of each helper library under `helpers_dir`.

    A SUBDIRECTORY is one MULTI-UNIT library: its entry point is the .sushi file that
    carries the directory's name, and the other .sushi files beside it are the units the
    entry imports (PL P0).
    """
    if not helpers_dir.exists():
        return []
    sources = list(helpers_dir.glob("*.sushi"))
    sources += [d / f"{d.name}.sushi" for d in helpers_dir.iterdir()
                if d.is_dir() and (d / f"{d.name}.sushi").exists()]
    return sorted(sources)


_LIBRARY_IMPORT = re.compile(r"^\s*(?:public\s+)?use\s+<lib/([A-Za-z0-9_]+)>", re.MULTILINE)
_UNIT_IMPORT = re.compile(r'^\s*(?:public\s+)?use\s+"([^"]*)"', re.MULTILINE)


def _directory_library_imports(directory: Path) -> Optional[Set[str]]:
    """Every `use <lib/NAME>` in a .sushi file under `directory`, or None when an import
    could reach a file outside it (a `..` step or an absolute unit path)."""
    names: Set[str] = set()
    for source in corpus_files(directory):
        text = source.read_bytes().decode("utf-8", errors="replace")
        for unit in _UNIT_IMPORT.findall(text):
            if unit.startswith("/") or ".." in Path(unit).parts:
                return None
        names.update(_LIBRARY_IMPORT.findall(text))
    return names


def helpers_for_selection(tests_dir: Path, fixtures: List[Path]) -> Optional[Set[str]]:
    """The helper libraries the selected fixtures can import, or None for all of them.

    A fixture reaches a helper only through a `use <lib/NAME>` line, in its own source or
    in a unit it imports. Every unit import in the corpus is a path relative to the
    fixture and goes down, so each .sushi file under the fixture's directory is read: its
    units, its `v2/` rebuild copy and its BUILD_LIB sources are all there. That is a
    superset of the reach. A unit import that can go up or out answers None, and the
    caller builds every helper.
    """
    helper_names = {s.stem for s in helper_sources(Path(tests_dir) / "libs" / "helpers")}
    needed: Set[str] = set()
    seen: Dict[Path, Optional[Set[str]]] = {}
    for fixture in fixtures:
        directory = Path(fixture).parent
        if directory not in seen:
            seen[directory] = _directory_library_imports(directory)
        names = seen[directory]
        if names is None:
            return None
        needed |= names & helper_names
    return needed


def _build_one_helper(sushic: Path, lib_file: Path, bin_dir: Path,
                      project_root: Path) -> Optional[str]:
    """Compile one helper library. Returns None on success, or the reason it failed."""
    name = lib_file.stem
    try:
        result = subprocess.run(
            # A .slib must state its own version (CE3505). These helpers are not
            # packages, so there is no nori.toml to read one from.
            [str(sushic), "--lib", "--lib-version", "0.0.0",
             "--lib-kind", "binary" if name in BINARY_ONLY_HELPERS else "source",
             str(lib_file), "-o", str(bin_dir / f"{name}.slib")],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60
        )
    except subprocess.TimeoutExpired:
        return f"Compilation of {name} timed out"
    except Exception as e:
        return f"Error compiling {name}: {e}"
    if result.returncode != 0:
        return f"Failed to compile {name}: {result.stderr}"
    return None


def build_test_helpers(project_root: Path, verbose: bool = False, jobs: int = 1,
                       only: Optional[Set[str]] = None) -> bool:
    """Build the test helper libraries, `jobs` at a time. Returns True on success.

    `only` names the helpers a selection needs (`helpers_for_selection`); None builds
    every helper. A helper outside `only` has its `.slib` removed, so that a library from
    an earlier run cannot answer for this one: a need that was not read fails loudly.
    Every failure is printed, and one failure fails the build.
    """
    helpers_dir = project_root / "tests" / "libs" / "helpers"
    bin_dir = project_root / "tests" / "libs" / "bin"
    sushic = project_root / "sushic"

    helper_files = helper_sources(helpers_dir)
    if not helper_files:
        if verbose:
            print("No test helper libraries found, skipping...")
        return True

    bin_dir.mkdir(parents=True, exist_ok=True)
    if only is not None:
        for lib_file in helper_files:
            if lib_file.stem not in only:
                (bin_dir / f"{lib_file.stem}.slib").unlink(missing_ok=True)
        helper_files = [f for f in helper_files if f.stem in only]

    if verbose:
        print(f"Building {len(helper_files)} test helper libraries...")

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        failures = [reason for reason in pool.map(
            lambda f: _build_one_helper(sushic, f, bin_dir, project_root), helper_files)
            if reason is not None]

    for reason in failures:
        print(reason)

    if verbose and not failures:
        print(f"  Libraries compiled to {bin_dir}")

    return not failures


DOC_GATE_MODULES_ENV = "SUSHI_DOC_GATE_MODULES"
# `path[:line:col]: warning|error [CODE]: ...` -- the head line of one diagnostic.
_DIAGNOSTIC_HEAD = re.compile(
    r"^(?P<path>[^:\s][^:]*?)(?::\d+:\d+)?: (?:warning|error) \[(?P<code>C[EW]\d{4})\]")
_DOC_CODE = re.compile(r"^C[EW]70\d\d$")
# A synthetic module is not in the registry, so the compiler must be told about it. This
# bootstrap adds it to the registry of ONE compiler process and runs that compiler; the
# SUSHI_DOC_GATE_MODULES override and a fixture's STDLIB_MODULE directive use it.
_OVERRIDE_BOOTSTRAP = """\
import json, sys
from pathlib import Path
from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
from sushi_lang.backend.stdlib_linker import StdlibLinker
for name, path in json.loads(sys.argv[1]).items():
    SOURCE_STDLIB_MODULES[name] = Path(path)
    StdlibLinker._virtual_units.add(name)
sys.argv = ["sushic", *sys.argv[2:]]
from sushi_lang.compiler import main
sys.exit(main())
"""


@dataclass
class DocGateResult:
    """What the stdlib doc-block gate or the dead-code gate found, or why it did not run."""
    modules: List[str] = field(default_factory=list)
    failures: Dict[str, List[str]] = field(default_factory=dict)
    skip_reason: Optional[str] = None
    duration: float = 0.0
    title: str = "Stdlib doc-block gate"
    noun: str = "module(s)"

    @property
    def ran(self) -> bool:
        return self.skip_reason is None

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_json(self) -> dict:
        return {"ran": self.ran, "skip_reason": self.skip_reason,
                "modules": self.modules, "failures": self.failures,
                "duration_seconds": round(self.duration, 2)}

    def report(self) -> List[str]:
        """The lines the text report prints."""
        if not self.ran:
            return [f"{self.title} SKIPPED: {self.skip_reason}."]
        lines = [f"{self.title}: {len(self.modules)} {self.noun}, "
                 f"{len(self.failures)} failed ({self.duration:.2f}s)"]
        for name, findings in sorted(self.failures.items()):
            lines.append(f"  {name}: {len(findings)} finding(s)")
            lines.extend(f"    {f}" for f in findings)
        return lines


def stdlib_override_command(project_root: Path, modules: Dict[str, Path],
                            compiler_args: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """A compiler command that first registers `modules` as Sushi-source stdlib modules,
    and the environment it needs on top of the caller's."""
    command = [sys.executable, "-c", _OVERRIDE_BOOTSTRAP,
               json.dumps({n: str(p) for n, p in modules.items()}), *compiler_args]
    env = {"PYTHONPATH": os.pathsep.join(
        [str(project_root), *filter(None, [os.environ.get("PYTHONPATH")])])}
    return command, env


def doc_gate_modules() -> Tuple[Dict[str, Path], bool]:
    """The modules the doc-block gate checks, and whether they are the test override.

    The default is SOURCE_STDLIB_MODULES. The override is a list of `.sushi` paths
    separated by `os.pathsep`, each module named `doc_gate/<file stem>`. Only the runner
    tests set it.
    """
    override = os.environ.get(DOC_GATE_MODULES_ENV)
    if override:
        paths = [Path(p).resolve() for p in override.split(os.pathsep) if p]
        return {f"doc_gate/{p.stem}": p for p in paths}, True
    from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
    return dict(SOURCE_STDLIB_MODULES), False


def _spelled_path(path: Path, project_root: Path) -> str:
    """A path as a `--filter` reads it: relative to the project root when it is under it."""
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _doc_gate_selection(modules: Dict[str, Path], project_root: Path,
                        filter_pattern: Optional[str], leaks_only: bool
                        ) -> Tuple[Dict[str, Path], Optional[str]]:
    """The modules a narrowed run checks, and the skip reason when it checks none.

    A `--filter` selects a module when the pattern is a substring of its registry name
    or of its path under the project root. `--leaks-only` selects no module, because the
    gate asserts no leak.
    """
    if leaks_only:
        return {}, "--leaks-only selects no stdlib module"
    if not filter_pattern:
        return modules, None

    chosen = {name: path for name, path in modules.items()
              if filter_pattern in name or filter_pattern in _spelled_path(path, project_root)}
    if not chosen:
        return {}, f"--filter {filter_pattern!r} selects no stdlib module"
    return chosen, None


def doc_gate_program(modules: Dict[str, Path]) -> str:
    """ONE program that imports every module, each behind an alias so no bare name clashes."""
    uses = [f"use <{name}> as dg_{name.replace('/', '_')}" for name in sorted(modules)]
    return "\n".join(["##: Imports every module the stdlib doc-block gate checks. :##", "",
                      *uses, "", "fn main() i32:", "    return 0", ""])


def _doc_findings(output: str, cwd: Path, modules: Dict[str, Path],
                  codes: re.Pattern = _DOC_CODE) -> Dict[str, List[str]]:
    """Every diagnostic of `codes` whose location is one of the checked module files.

    The compiler prints a location relative to its working directory, so `cwd` must hold
    every module file; outside it the compiler prints the bare file name.
    """
    owner = {path.resolve(): name for name, path in modules.items()}
    found: Dict[str, List[str]] = {}
    for line in output.splitlines():
        head = _DIAGNOSTIC_HEAD.match(line.strip())
        if head is None or not codes.match(head["code"]):
            continue
        name = owner.get((cwd / head["path"]).resolve())
        if name is not None:
            found.setdefault(name, []).append(line.strip())
    return found


def stdlib_doc_gate(project_root: Path, filter_pattern: Optional[str] = None,
                    leaks_only: bool = False) -> DocGateResult:
    """Check the doc blocks of every bundled Sushi-source stdlib module (#953).

    The compiler does not lint an injected stdlib unit, and a stdlib module is never
    built alone. So the gate compiles ONE program that imports every module, with
    SUSHI_STDLIB_DOC_GATE set: the `docs` pass then checks the bundled units too.
    """
    from sushi_lang.semantics.semantic_analyzer import STDLIB_DOC_GATE_ENV

    every, is_override = doc_gate_modules()
    chosen, skip = _doc_gate_selection(every, project_root, filter_pattern, leaks_only)
    if skip is not None:
        return DocGateResult(skip_reason=skip)

    start = time.time()
    result = DocGateResult(modules=sorted(chosen))
    with tempfile.TemporaryDirectory(prefix="sushi_doc_gate_") as tmp:
        program = Path(tmp) / "doc_gate.sushi"
        program.write_text(doc_gate_program(chosen), encoding="utf-8")
        flags = ["--warn-missing-docs", str(program), "-o", str(Path(tmp) / "doc_gate"),
                 "--cache-dir", str(Path(tmp) / "cache")]
        env = {**os.environ, STDLIB_DOC_GATE_ENV: "1"}
        cwd = project_root
        if is_override:
            command, extra = stdlib_override_command(project_root, chosen, flags)
            cwd = Path(os.path.commonpath([p.parent for p in chosen.values()]))
            env.update(extra)
        else:
            command = [str(project_root / "sushic"), *flags]
        try:
            done = subprocess.run(command, cwd=cwd, capture_output=True,
                                  text=True, timeout=600, env=env)
        except subprocess.TimeoutExpired:
            result.failures["(the gate program)"] = ["the compilation timed out"]
            result.duration = time.time() - start
            return result
    output = done.stdout + done.stderr
    result.failures.update(_doc_findings(output, cwd, chosen))
    if done.returncode not in (0, 1):
        # A program that does not compile is a program whose doc blocks were not all checked.
        errors = [line.strip() for line in output.splitlines() if " error [" in line]
        result.failures["(the gate program)"] = [
            f"the compilation failed with exit {done.returncode}: "
            f"{errors[0] if errors else 'no diagnostic printed'}"]
    result.duration = time.time() - start
    return result


# The dead-code gate (#959, ruling 5): the two `--warn-unused` codes.
_DEAD_CODE = re.compile(r"^CW(?:1004|3006)$")
# A list of program paths, separated by `os.pathsep`, that replaces the programs under
# `toolchain/src/`. Only the runner tests set it.
DEAD_GATE_PROGRAMS_ENV = "SUSHI_DEAD_GATE_PROGRAMS"


def dead_gate_programs(project_root: Path) -> List[Path]:
    """The programs the dead-code gate compiles: each `toolchain/src/` file with a main."""
    override = os.environ.get(DEAD_GATE_PROGRAMS_ENV)
    if override is not None:
        return [Path(p).resolve() for p in override.split(os.pathsep) if p]
    return sorted(path for path in (project_root / "toolchain" / "src").glob("*.sushi")
                  if re.search(r"^fn main\(", path.read_text(encoding="utf-8"), re.M))


def _dead_gate_compile(command: List[str], cwd: Path, env: Dict[str, str],
                       label: str, result: DocGateResult) -> str:
    """One compilation of the dead-code gate; its output, and a failure when it did not build."""
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              timeout=600, env=env)
    except subprocess.TimeoutExpired:
        result.failures[label] = ["the compilation timed out"]
        return ""
    output = done.stdout + done.stderr
    if done.returncode not in (0, 1):
        # A program that does not compile is a program the lint did not finish.
        errors = [line.strip() for line in output.splitlines() if " error [" in line]
        result.failures[label] = [
            f"the compilation failed with exit {done.returncode}: "
            f"{errors[0] if errors else 'no diagnostic printed'}"]
    return output


def stdlib_dead_code_gate(project_root: Path, filter_pattern: Optional[str] = None,
                          leaks_only: bool = False) -> DocGateResult:
    """Fail on a `--warn-unused` finding in the bundled stdlib or in `toolchain/src/` (#959).

    The facts come from the compiler. The gate compiles the program that imports every
    stdlib module with SUSHI_STDLIB_DEAD_GATE set, so the lint checks the bundled units
    too, and each toolchain program; a finding located in a checked file fails the run.
    """
    from sushi_lang.semantics.semantic_analyzer import STDLIB_DEAD_GATE_ENV

    result = DocGateResult(title="Dead-code gate", noun="module(s) and program(s)")
    every, is_override = doc_gate_modules()
    chosen, _ = _doc_gate_selection(every, project_root, filter_pattern, leaks_only)
    programs = [] if leaks_only else [
        path for path in dead_gate_programs(project_root)
        if not filter_pattern or filter_pattern in _spelled_path(path, project_root)]
    if not chosen and not programs:
        result.skip_reason = ("--leaks-only selects no stdlib module or toolchain program"
                              if leaks_only else
                              f"--filter {filter_pattern!r} selects no stdlib module or "
                              "toolchain program")
        return result

    start = time.time()
    result.modules = [*sorted(chosen), *(p.name for p in programs)]
    with tempfile.TemporaryDirectory(prefix="sushi_dead_gate_") as tmp:
        if chosen:
            program = Path(tmp) / "dead_gate.sushi"
            program.write_text(doc_gate_program(chosen), encoding="utf-8")
            flags = ["--warn-unused", str(program), "-o", str(Path(tmp) / "dead_gate"),
                     "--cache-dir", str(Path(tmp) / "cache")]
            env = {**os.environ, STDLIB_DEAD_GATE_ENV: "1"}
            cwd = project_root
            if is_override:
                command, extra = stdlib_override_command(project_root, chosen, flags)
                cwd = Path(os.path.commonpath([p.parent for p in chosen.values()]))
                env.update(extra)
            else:
                command = [str(project_root / "sushic"), *flags]
            output = _dead_gate_compile(command, cwd, env, "(the stdlib program)", result)
            result.failures.update(_doc_findings(output, cwd, chosen, _DEAD_CODE))
        for index, path in enumerate(programs):
            flags = ["--warn-unused", str(path), "-o", str(Path(tmp) / f"program_{index}"),
                     "--cache-dir", str(Path(tmp) / "cache")]
            output = _dead_gate_compile([str(project_root / "sushic"), *flags], path.parent,
                                        dict(os.environ), path.name, result)
            units = {p.name: p for p in path.parent.glob("*.sushi")}
            result.failures.update(_doc_findings(output, path.parent, units, _DEAD_CODE))
    result.duration = time.time() - start
    return result


LIB_READER_GATE_PATH = "stdlib/slib/lib_reader/"
# A directory that holds a `slib-info` to run in place of the one the gate builds. Only
# the runner tests set it, to show that the gate fails a tool that prints no code.
LIB_READER_TOOL_ENV = "SUSHI_LIB_READER_GATE_TOOL_BIN"

_GATE_LIBRARY = """\
##: The library the library-reader gate damages. :##

##:
Adds two numbers.

- Parameter a: The first addend.
- Parameter b: The second addend.
- Returns: The sum.
:##
public fn add(i32 a, i32 b) i32:
    return a + b

##:
Gives its argument back.

- Parameter value: The value.
- Returns: The value.
:##
public fn same@(T)(nom T value) T:
    return value

##: A box that holds one value. :##
public struct Box@(T):
    ##: The value. :##
    T item

##: The answer. :##
public const i32 ANSWER = 42
"""

_GATE_CONSUMER = """\
use <lib/{name}>

fn main() i32:
    let i32 n = add(40, 2)
    println(same(nom n))
    return 0
"""


def _slib_manifest(raw: bytes) -> Tuple[dict, int]:
    """The metadata map of a `.slib` image, and the offset where its metadata ends."""
    import msgpack
    import struct

    meta_len = struct.unpack("<Q", raw[44:52])[0]
    return msgpack.unpackb(raw[52:52 + meta_len], raw=False), 52 + meta_len


def _edited_slib(raw: bytes, edit) -> bytes:
    """A copy of a `.slib` image whose metadata map `edit` changed in place."""
    import msgpack
    import struct

    meta, end = _slib_manifest(raw)
    edit(meta)
    blob = msgpack.packb(meta, use_bin_type=True)
    return raw[:44] + struct.pack("<Q", len(blob)) + blob + raw[end:]


def _drop(*keys):
    """An edit that deletes the key at the end of a path of keys and list indices."""
    def edit(meta):
        node = meta
        for key in keys[:-1]:
            node = node[key]
        del node[keys[-1]]
    return edit


def _put(value, *keys):
    """An edit that sets the key at the end of a path of keys and list indices."""
    def edit(meta):
        node = meta
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return edit


def _write_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _half(source: Path) -> bytes:
    raw = source.read_bytes()
    return raw[:len(raw) // 2]


def _meta_len(source: Path, length: int) -> bytes:
    """A copy whose header declares a metadata section of `length` bytes."""
    import struct

    raw = source.read_bytes()
    return raw[:44] + struct.pack("<Q", length) + raw[52:]


def _container_version(source: Path, version: int) -> bytes:
    """A copy whose header states container `version`."""
    import struct

    raw = source.read_bytes()
    return raw[:16] + struct.pack("<I", version) + raw[20:]


def _oversize(source: Path, target: Path) -> Path:
    """A whole library followed by a hole, one byte past the 1 GiB limit (sparse)."""
    from sushi_lang.backend.library_format import LibraryFormat

    target.write_bytes(source.read_bytes())
    with open(target, "r+b") as f:
        f.truncate(LibraryFormat.MAX_FILE_SIZE + 1)
    return target


def _write_edit(source: Path, case_dir: Path, edit) -> Path:
    return _write_bytes(case_dir / source.name, _edited_slib(source.read_bytes(), edit))


# (case, expected code or None for a clean run, how the input is made). A maker takes
# the directory of the built libraries and the case's own directory, and gives the path
# the command reads. `--lib-info` runs each case in both halves.
# The versions before the bare-function change: their signatures without `| E` meant
# `| StdError`, so a reader must refuse them rather than read them as bare.
_OLD_CONTAINER = 4
_OLD_TEMPLATES = 7

LIB_INFO_CASES = (
    ("a whole library", None, lambda libs, d: libs / "gate_lib.slib"),
    ("no library_name", "CE3512", lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _drop("library_name"))),
    ("no platform", "CE3512", lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _drop("platform"))),
    ("templates is a string", "CE3512", lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _put("x", "templates"))),
    ("a function record with no name", "CE3512", lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _drop("public_functions", 0, "name"))),
    ("a function record that does not state its channel", "CE3512",
     lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _drop("public_functions", 0, "has_channel"))),
    ("a parameter type that is a number", "CE3512", lambda libs, d: _write_edit(
        libs / "gate_lib.slib", d, _put(7, "public_functions", 0, "params", 0, "type"))),
    ("a bad magic", "CE3508", lambda libs, d: _write_bytes(
        d / "gate_lib.slib", b"NOTASLIB" * 8)),
    ("no such file", "CE3515", lambda libs, d: d / "gate_lib.slib"),
    ("not a .slib name", "CE3516", lambda libs, d: _write_bytes(
        d / "gate_lib.txt", (libs / "gate_lib.slib").read_bytes())),
    ("cut to half", "CE3511", lambda libs, d: _write_bytes(
        d / "gate_lib.slib", _half(libs / "gate_lib.slib"))),
    ("a short file with a bad magic", "CE3508", lambda libs, d: _write_bytes(
        d / "gate_lib.slib", b"hello!")),
    ("a metadata length past the end", "CE3510", lambda libs, d: _write_bytes(
        d / "gate_lib.slib", _meta_len(libs / "gate_lib.slib", 1 << 40))),
    ("a file past MAX_FILE_SIZE", "CE3513", lambda libs, d: _oversize(
        libs / "gate_lib.slib", d / "gate_lib.slib")),
    ("an older container version", "CE3509", lambda libs, d: _write_bytes(
        d / "gate_lib.slib", _container_version(libs / "gate_lib.slib", _OLD_CONTAINER))),
)

def _append(text: str, *keys):
    """An edit that appends `text` to the string at the end of a path of keys."""
    def edit(meta):
        node = meta
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] += text
    return edit


_SECOND_DECLARATION = "\npublic fn extra() i32:\n    return 0\n"

# (case, expected code or None, the edit of the binary library's manifest). A consumer
# that imports the edited library is compiled for each case.
CONSUMER_CASES = (
    ("a whole library", None, None),
    ("an older templates schema", "CE3512", _put(_OLD_TEMPLATES, "templates", "version")),
    ("a generic function record with no name", "CE3512",
     _drop("templates", "generic_functions", 0, "name")),
    ("no library_name", "CE3512", _drop("library_name")),
    ("templates is a string", "CE3512", _put("x", "templates")),
    ("a generic function source that does not parse", "CE3512",
     _put("fn broken@(T)(T a T:\n", "templates", "generic_functions", 0, "source")),
    ("a generic function source with two declarations", "CE3512",
     _append(_SECOND_DECLARATION, "templates", "generic_functions", 0, "source")),
    ("a generic struct source that does not parse", "CE3512",
     _put("public struct Box@(T:\n", "templates", "generic_structs", 0, "source")),
    ("a constant source with two declarations", "CE3512",
     _append("\npublic const i32 OTHER = 1\n", "public_constants", 0, "source")),
    ("a bare function record that states a channel", "CE3512",
     _put(True, "public_functions", 0, "has_channel")),
)


@dataclass
class ReaderGateResult:
    """What the library-reader gate found, or why it did not run."""
    checks: int = 0
    failures: List[str] = field(default_factory=list)
    skip_reason: Optional[str] = None
    duration: float = 0.0
    title: str = "Library-reader gate"

    @property
    def ran(self) -> bool:
        return self.skip_reason is None

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_json(self) -> dict:
        return {"ran": self.ran, "skip_reason": self.skip_reason, "checks": self.checks,
                "failures": self.failures, "duration_seconds": round(self.duration, 2)}

    def report(self) -> List[str]:
        """The lines the text report prints."""
        if not self.ran:
            return [f"{self.title} SKIPPED: {self.skip_reason}."]
        lines = [f"{self.title}: {self.checks} check(s), "
                 f"{len(self.failures)} failed ({self.duration:.2f}s)"]
        lines.extend(f"  {f}" for f in self.failures)
        return lines


def _gate_verdict(done: subprocess.CompletedProcess, code: Optional[str]) -> Optional[str]:
    """Why one run is wrong, or None. A clean run exits 0; a fault exits 2 with its code."""
    output = done.stdout + done.stderr
    if "Traceback" in output:
        return "printed a Python traceback"
    if code is None:
        return None if done.returncode == 0 else f"exit {done.returncode}, expected 0"
    if done.returncode != 2:
        return f"exit {done.returncode}, expected 2 with {code}"
    if f"error [{code}]" not in done.stderr:
        heads = [line.strip() for line in done.stderr.splitlines() if " [C" in line]
        got = heads[0] if heads else done.stderr.strip()[:160]
        return f"no {code} on stderr (got: {got})"
    for stray in ("CE0000", "CE6001"):
        if stray != code and f"[{stray}]" in output:
            return f"also printed {stray}"
    return None


def _gate_skip(result: ReaderGateResult, filter_pattern: Optional[str],
               leaks_only: bool) -> Optional[ReaderGateResult]:
    """The result that says why a library-reader step does not run, or None."""
    if leaks_only:
        result.skip_reason = "--leaks-only selects no library-reader check"
    elif filter_pattern and filter_pattern not in LIB_READER_GATE_PATH:
        result.skip_reason = f"--filter {filter_pattern!r} selects no library-reader check"
    return result if result.skip_reason is not None else None


def _gate_run(command: List[str], cwd: Path,
              env: Dict[str, str]) -> subprocess.CompletedProcess:
    """One command of a library-reader step, with the toolchain choice left to `env`."""
    base_env = {k: v for k, v in os.environ.items()
                if k not in ("SUSHI_TOOLCHAIN", "SUSHI_TOOLCHAIN_BIN")}
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              timeout=120, env={**base_env, **env})
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, -1, "", "timed out after 120s")


def _gate_tool(project_root: Path, tmp: Path, result: ReaderGateResult) -> str:
    """The directory of the `slib-info` a step runs: `LIB_READER_TOOL_ENV`, or one built here."""
    tool_bin = os.environ.get(LIB_READER_TOOL_ENV)
    if tool_bin:
        return tool_bin
    (tmp / "bin").mkdir()
    built = _gate_run([str(project_root / "sushic"),
                       str(project_root / "toolchain" / "src" / "slib_info.sushi"),
                       "-o", str(tmp / "bin" / "slib-info"), "--cache-dir", str(tmp / "cache")],
                      project_root, {})
    if built.returncode != 0:
        result.failures.append(f"the slib-info tool does not build: "
                               f"{built.stderr.strip()[:200]}")
    return str(tmp / "bin")


def lib_reader_gate(project_root: Path, filter_pattern: Optional[str] = None,
                    leaks_only: bool = False) -> ReaderGateResult:
    """Run `--lib-info` in both halves, and a consumer, over damaged `.slib` files.

    A fixture cannot use `--lib-info`, and a damaged binary library cannot be a helper,
    so the gate builds the libraries, damages copies of them, and reads each copy. The
    Sushi half is the `slib-info` tool built here from `toolchain/src/`; the Python half
    is the same command with SUSHI_TOOLCHAIN=off.
    """
    skipped = _gate_skip(ReaderGateResult(), filter_pattern, leaks_only)
    if skipped is not None:
        return skipped

    start = time.time()
    result = ReaderGateResult()
    sushic = str(project_root / "sushic")
    run = _gate_run

    with tempfile.TemporaryDirectory(prefix="sushi_lib_reader_gate_") as tmp_name:
        tmp = Path(tmp_name)
        cache = str(tmp / "cache")
        libs = tmp / "libs"
        libs.mkdir()
        (tmp / "gate_lib.sushi").write_text(_GATE_LIBRARY, encoding="utf-8")
        tool_bin = _gate_tool(project_root, tmp, result)
        for kind, name in (("hybrid", "gate_lib"), ("binary", "gate_bin")):
            built = run([sushic, "--lib", "--lib-version", "1.0.0", "--lib-kind", kind,
                         str(tmp / "gate_lib.sushi"), "-o", str(libs / f"{name}.slib"),
                         "--cache-dir", cache], tmp, {})
            if built.returncode != 0:
                result.failures.append(f"the {kind} gate library does not build: "
                                       f"{built.stderr.strip()[:200]}")
        if result.failures:
            result.duration = time.time() - start
            return result

        halves = (("tool", {"SUSHI_TOOLCHAIN_BIN": tool_bin}),
                  ("python", {"SUSHI_TOOLCHAIN": "off"}))
        for index, (case, code, make) in enumerate(LIB_INFO_CASES):
            case_dir = tmp / f"info_{index}"
            case_dir.mkdir()
            path = make(libs, case_dir)
            for half, env in halves:
                done = run([sushic, "--lib-info", str(path)], case_dir, env)
                result.checks += 1
                fault = _gate_verdict(done, code)
                if fault:
                    result.failures.append(f"--lib-info ({half}), {case}: {fault}")

        for index, (case, code, edit) in enumerate(CONSUMER_CASES):
            case_dir = tmp / f"use_{index}"
            case_dir.mkdir()
            raw = (libs / "gate_bin.slib").read_bytes()
            (case_dir / "gate_bin.slib").write_bytes(
                raw if edit is None else _edited_slib(raw, edit))
            program = case_dir / "main.sushi"
            program.write_text(_GATE_CONSUMER.format(name="gate_bin"), encoding="utf-8")
            done = run([sushic, str(program), "-o", str(case_dir / "main"),
                        "--cache-dir", cache], case_dir, {"SUSHI_LIB_PATH": str(case_dir)})
            result.checks += 1
            fault = _gate_verdict(done, code)
            if fault:
                result.failures.append(f"a consumer, {case}: {fault}")
    result.duration = time.time() - start
    return result


_REPORT_LIBRARY = """\
use <collections/strings>

##: A colour. :##
public enum Colour:
    ##: No payload. :##
    Red
    ##: Two payloads. :##
    Blue(string, i32)

##: A fault. :##
public error ReportFault:
    ##: No payload. :##
    Missing
    ##: One payload. :##
    Bad(i32)

##: A fault that holds a fault. :##
public error ReportWrap:
    ##: The fault it holds. :##
    Held(ReportFault)

##: Wraps a fault. :##
extend ReportFault as ReportWrap:
    return ReportWrap.Held(self)

##:
Checks that a number is even.

- Parameter n: The number.
- Returns: `n`.
- Errors: `ReportFault.Bad` for an odd number.
:##
public fn even(i32 n) i32 | ReportFault:
    if (n % 2 == 1):
        return Result.Err(ReportFault.Bad(n))
    return Result.Ok(n)

##: A fault that carries a value. :##
public error ReportDecode@(T):
    ##: The value. :##
    Bad(T)
    ##: No payload. :##
    Truncated

##: A thing with a name. :##
public perk Named:
    ##:
    The name.

    - Returns: The name.
    :##
    fn name() string

##:
Takes a value with two constraints.

- Parameter x: The value.
- Returns: The length of its name.
:##
public fn both@(T: Hashable + Named)(T x) i32:
    return x.name().len()

##:
A blue colour.

- Parameter n: The number it carries.
- Returns: The colour.
:##
public fn paint(i32 n) Colour:
    return Colour.Blue("blue", n)

##:
The first number of an array.

- Parameter xs: The numbers.
- Returns: The first number.
:##
public fn head(i32[] xs) i32 dont_panic because "the caller passes a non-empty array":
    return xs[0]

##:
A blue colour that carries the first code.

- Parameter codes: The codes.
- Returns: The colour.
:##
extend Colour static pick(i32[] codes) Colour dont_panic because "the caller passes a \\"non-empty\\" array":
    return Colour.Blue("pick", codes[0])

##:
Halves an even number.

- Parameter n: The number.
- Returns: Half of `n`.
- Errors: `StdError.Error` for an odd number.
:##
public fn half(i32 n) i32 | StdError:
    if (n % 2 == 1):
        return Result.Err(StdError.Error)
    return Result.Ok(n / 2)

##:
Splits a number in two halves.

- Parameter n: The number.
- Returns: The halves, as a tuple with a nested tuple.
:##
public fn split(i32 n) ((i32, i32), string):
    return ((n / 2, n - n / 2), "split")

##:
Counts a pack.

- Parameter args: The values.
- Returns: One.
:##
public fn lcount@(...Ts: Display)(...Ts args) i32:
    return 1

##:
Takes a value before a pack.

- Parameter first: The leading value.
- Parameter rest: The values.
- Returns: Two.
:##
public fn lead@(T, ...Ts: Display)(T first, ...Ts rest) i32:
    first
    return 2

##: A crate that holds one value. :##
public struct Crate@(T):
    ##: The value. :##
    T value

##:
Tells the crate of a clonable value.

- Returns: One.
:##
extend Crate@(T: Clone) clone_count() i32:
    return 1

##: A crate of a hashable value has a name. :##
extend Crate@(T: Hashable) with Named:
    ##:
    The name.

    - Returns: The name.
    :##
    fn name() string:
        return "crate"
"""

_REPORT_CONSUMER = """\
use <lib/{name}>

fn wrapped(i32 n) i32 | ReportWrap:
    let i32 v = even(n)??
    return Result.Ok(v)

fn main() i32:
    match paint(42):
        Colour.Blue(s, n) -> println("{{s}} {{n}}")
        Colour.Red -> println("red")
    match Colour.Blue("made", 7):
        Colour.Blue(s, n) -> println("{{s}} {{n}}")
        Colour.Red -> println("red")
    println(half(8).realise(-1))
    println(half(7).realise(-1))
    println(head(from([5, 6])))
    match Colour.pick(from([9])):
        Colour.Blue(s, n) -> println("{{s}} {{n}}")
        Colour.Red -> println("red")
    match wrapped(3):
        Result.Ok(v) -> println("ok {{v}}")
        Result.Err(e) -> println("{{e}}")
    return 0
"""

# Whole lines the `--lib-info` report of `_REPORT_LIBRARY` must hold, in both halves (#966);
# the fifth is a tuple, which neither half may print in its interned `$Tuple<...>` form, the
# sixth and the seventh keep the `...` of a type pack (#1164), the eighth and the ninth
# print the keyword `error` for a concrete and for a generic error type, the tenth is a
# conversion, which the consumer calls through `??`, the next two put a bound in a
# target (#1070), and the last two are `dont_panic` declarations with their reasons
# (docs/design/dont-panic.md, D10).
REPORT_LINES = (
    "  fn both@(T: Hashable + Named)(T x) i32",
    "    Blue(string, i32)",
    "  fn paint(i32 n) Colour",
    "  fn half(i32 n) i32 | StdError",
    "  fn split(i32 n) ((i32, i32), string)",
    "  fn lcount@(...Ts: Display)(...Ts args) i32",
    "  fn lead@(T, ...Ts: Display)(T first, ...Ts rest) i32",
    "  error ReportFault:",
    "  error ReportDecode@(T):",
    "  extend ReportFault as ReportWrap",
    "  extend Crate@(T: Clone) clone_count() i32",
    "  extend Crate@(T: Hashable) with Named:",
    '  head dont_panic because "the caller passes a non-empty array"',
    '  Colour.pick dont_panic because "the caller passes a \\"non-empty\\" array"',
)
REPORT_KINDS = ("source", "hybrid", "binary")
REPORT_CONSUMER_STDOUT = ("blue 42\nmade 7\n4\n-1\n5\npick 9\n"
                          "ReportWrap.Held(ReportFault.Bad(3))\n")
# The kinds whose consumer re-parses the marked bodies of `_REPORT_LIBRARY`, and so must
# consent with `--dont-panic` (D9). A hybrid or a binary library ships a concrete body as
# bitcode, and the flag there marks nothing (CW0003).
REPORT_CONSENT_KINDS = ("source",)


def lib_info_report_gate(project_root: Path, filter_pattern: Optional[str] = None,
                         leaks_only: bool = False) -> ReaderGateResult:
    """Read the `--lib-info` report of one library in both halves, and use the library.

    A fixture cannot use `--lib-info`. The step builds `_REPORT_LIBRARY` in each kind,
    asks both halves for its report and looks for each of `REPORT_LINES`, then compiles
    and runs a consumer of each kind that binds both payloads of a variant and calls the
    conversion of the library through `??`.
    """
    result = ReaderGateResult(title="Library-report gate")
    if _gate_skip(result, filter_pattern, leaks_only) is not None:
        return result

    start = time.time()
    sushic = str(project_root / "sushic")
    with tempfile.TemporaryDirectory(prefix="sushi_lib_report_gate_") as tmp_name:
        tmp = Path(tmp_name)
        cache = str(tmp / "cache")
        tool_bin = _gate_tool(project_root, tmp, result)
        (tmp / "report_lib.sushi").write_text(_REPORT_LIBRARY, encoding="utf-8")
        for kind in REPORT_KINDS:
            kind_dir = tmp / kind
            kind_dir.mkdir()
            lib = kind_dir / "report_lib.slib"
            built = _gate_run([sushic, "--lib", "--lib-version", "1.0.0", "--lib-kind", kind,
                               "--dont-panic", str(tmp / "report_lib.sushi"), "-o", str(lib),
                               "--cache-dir", cache], tmp, {})
            if built.returncode != 0:
                result.failures.append(f"the {kind} report library does not build: "
                                       f"{built.stderr.strip()[:200]}")
                continue
            for half, env in (("tool", {"SUSHI_TOOLCHAIN_BIN": tool_bin}),
                              ("python", {"SUSHI_TOOLCHAIN": "off"})):
                done = _gate_run([sushic, "--lib-info", str(lib)], kind_dir, env)
                lines = done.stdout.splitlines()
                for expected in REPORT_LINES:
                    result.checks += 1
                    if done.returncode != 0 or expected not in lines:
                        result.failures.append(
                            f"--lib-info ({half}), {kind}: no line {expected!r} "
                            f"(exit {done.returncode})")
            program = kind_dir / "main.sushi"
            program.write_text(_REPORT_CONSUMER.format(name="report_lib"), encoding="utf-8")
            consent = ["--dont-panic"] if kind in REPORT_CONSENT_KINDS else []
            done = _gate_run([sushic, str(program), "-o", str(kind_dir / "main"), *consent,
                              "--cache-dir", cache], kind_dir, {"SUSHI_LIB_PATH": str(kind_dir)})
            result.checks += 1
            if done.returncode != 0:
                result.failures.append(f"a consumer, {kind}: exit {done.returncode}: "
                                       f"{done.stderr.strip()[:200]}")
                continue
            ran = _gate_run([str(kind_dir / "main")], kind_dir, {})
            if ran.stdout != REPORT_CONSUMER_STDOUT:
                result.failures.append(f"a consumer, {kind}: printed {ran.stdout!r}")
    result.duration = time.time() - start
    return result


def leakcheck_platform() -> Optional[str]:
    """The interposer's platform key, or None where leak checking is not supported.

    Two platforms, named explicitly. Mapping "not macOS" to Linux meant an unsupported
    platform looked for `bin/linux/leakcheck.so`, did not find it, and reported the leak
    check as "interposer not built" -- a reason that is not the real one (#275).
    """
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def leakcheck_lib_path(project_root: Path) -> Optional[Path]:
    """Path to the precompiled malloc-interposer, or None on an unsupported platform."""
    plat = leakcheck_platform()
    if plat is None:
        return None
    ext = "dylib" if plat == "darwin" else "so"
    return project_root / "tests" / "leakcheck" / "bin" / plat / f"leakcheck.{ext}"


def build_leakcheck(project_root: Path, verbose: bool = False) -> bool:
    """Compile the malloc-interposer the leak gate preloads (macOS + Linux)."""
    out = leakcheck_lib_path(project_root)
    if out is None:
        print(f"Leak checking is not supported on {sys.platform}")
        return False

    src = project_root / "tests" / "leakcheck" / "leakcheck.c"
    if not src.exists():
        print(f"Leak-check source not found: {src}")
        return False

    out.parent.mkdir(parents=True, exist_ok=True)

    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return True

    if leakcheck_platform() == "darwin":
        cmd = ["cc", "-dynamiclib", "-O1", "-o", str(out), str(src)]
    else:
        cmd = ["cc", "-shared", "-fPIC", "-O1", "-o", str(out), str(src), "-ldl"]

    if verbose:
        print(f"Building leak-check interposer: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=True,
                                text=True, timeout=60)
        if result.returncode != 0:
            print(f"Failed to build leak-check interposer: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("Leak-check interposer build timed out")
        return False
    except Exception as e:
        print(f"Error building leak-check interposer: {e}")
        return False


# Tests that expose a known compiler crash (ICE / assertion failure in the
# backend) rather than a user-visible compilation error. These fail compilation
# with exit 2 despite being named test_*.sushi, so they cannot satisfy the
# "expect exit 0" rule until the underlying bug is fixed.
# Each entry links to the tracking note in tests/enhanced_test_runner.py.
COMPILATION_QUARANTINE: set[str] = set()



def arm_spelling_gate() -> None:
    """Turn the diagnostic spelling gate on for every compiler the run spawns (#734).

    The gate refuses an interned type name (`List<i32>`) in a diagnostic, where the
    source spelling is `List@(i32)`. It is off in the compiler by default, because a
    cosmetic fault must never become a user-facing crash. A test run is exactly the
    place that wants it loud.

    Both runners call this, and both also read the compiler's stderr for the gate's
    own name: the gate raises, the process dies with a traceback, and an exit code
    that happens to match the fixture's expectation must not read as a pass.
    """
    os.environ[SPELLING_GATE_ENV] = "1"


def spelling_gate_tripped(stderr: str) -> bool:
    """Did the spelling gate refuse a diagnostic this compiler printed?"""
    return f"{SPELLING_GATE_ENV}:" in (stderr or "")



def build_parser() -> argparse.ArgumentParser:
    """The front end's options. Each one SELECTS fixtures or shapes the report; none weakens a check."""
    # allow_abbrev=False: with --leaks deleted, argparse would otherwise accept it as a
    # unique prefix of --leaks-only and silently narrow a full run to the leak subset.
    # A removed flag has to fail, not quietly mean something else.
    parser = argparse.ArgumentParser(description="Run Sushi language compiler tests",
                                     allow_abbrev=False)
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Show detailed output for each test")
    parser.add_argument("-j", "--jobs", type=int, default=default_jobs(),
                       help=f"Number of parallel test jobs (default: {DEFAULT_JOBS}, "
                            f"or ${JOBS_ENV_VAR})")
    parser.add_argument("--filter", type=str,
                       help="Only run tests whose path under tests/ contains this string")
    parser.add_argument("--enhanced", action="store_true",
                       help="Accepted and does nothing: every run is the enhanced run. "
                            "Kept so the CI lines and a typed habit keep working")
    parser.add_argument("--json", action="store_true",
                       help="Output results in JSON format")
    parser.add_argument("--skip-build", action="store_true",
                       help="Skip building stdlib and test helpers")
    parser.add_argument("--leaks-only", action="store_true",
                       help="Run only the tests declaring EXPECT_NO_LEAKS (a selector; "
                            "the assertion is enforced by every run)")
    parser.add_argument("--allow-leak-skips", action="store_true",
                       help="Report a pass even though leak assertions were not "
                            "evaluated. Only for a platform that cannot check at all")
    parser.add_argument("--compile-only", action="store_true",
                       help="Run only the fixtures that never execute a binary (every "
                            "test_err_ and most test_warn_). A selector, not a weaker "
                            "check: what it selects is asserted in full")
    parser.add_argument("--fresh-processes", action="store_true",
                       help="Start a fresh sushic process for every compile, not a child "
                            "of the fork-server. An execution strategy: the answer is "
                            "the same, and every check runs")

    return parser


def main():
    """The front end: build what a run needs, then hand it to the ONE runner.

    `run_tests.py` used to carry a second runner of its own that compiled every fixture
    and checked the compiler's exit status alone -- it read no `EXPECT_*` directive, so a
    `test_err_` fixture passed it whatever diagnostic the compiler printed (#760). It was
    not the compile HALF of the suite; it was the whole suite with the assertions turned
    off, and it bought no speed for them: over `tests/diagnostics/`, where almost nothing
    runs a binary, it measured 6.95s against the enhanced runner's 6.85s.

    What remains here is what both halves always shared -- the stdlib and helper builds,
    the leak interposer and the spelling gate -- plus the argument
    parsing. `enhanced_test_runner` runs the tests.
    """
    args = build_parser().parse_args()

    arm_spelling_gate()

    import enhanced_test_runner
    sys.argv = [sys.argv[0]]
    if args.verbose:
        sys.argv.append("--verbose")
    if args.filter:
        sys.argv.extend(["--filter", args.filter])
    sys.argv.extend(["--jobs", str(args.jobs)])
    if args.json:
        sys.argv.append("--json")
    if args.skip_build:
        sys.argv.append("--skip-build")
    if args.leaks_only:
        sys.argv.append("--leaks-only")
    if args.allow_leak_skips:
        sys.argv.append("--allow-leak-skips")
    if args.compile_only:
        sys.argv.append("--compile-only")
    if args.fresh_processes:
        sys.argv.append("--fresh-processes")
    rc = enhanced_test_runner.main()

    # After the run, which builds the stdlib the gate's libraries import. The report
    # goes to stderr under --json, so stdout stays one JSON document.
    gate = lib_reader_gate(Path(__file__).resolve().parent.parent,
                           filter_pattern=args.filter, leaks_only=args.leaks_only)
    report_gate = lib_info_report_gate(Path(__file__).resolve().parent.parent,
                                       filter_pattern=args.filter,
                                       leaks_only=args.leaks_only)
    dead_gate = stdlib_dead_code_gate(Path(__file__).resolve().parent.parent,
                                      filter_pattern=args.filter, leaks_only=args.leaks_only)
    for line in gate.report() + report_gate.report() + dead_gate.report():
        print(line, file=sys.stderr if args.json else sys.stdout)
    return rc or (0 if gate.passed and report_gate.passed and dead_gate.passed else 1)


if __name__ == "__main__":
    sys.exit(main())
