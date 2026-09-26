#!/usr/bin/env python3
"""Comprehensive test runner for Sushi language compiler."""

import argparse
import json
import re
import subprocess
import sys
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sushi_lang.internals.report import SPELLING_GATE_ENV  # noqa: E402


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
BINARY_ONLY_HELPERS = {"private_closure_lib", "kept_private_lib", "const_lib", "var_bin_lib",
                       "mangle_closure_lib", "twolib_bin", "channel_bin_lib",
                       "generic_perk_bin_lib", "binary_api_lib", "private_generic_type_lib",
                       "generic_ext_bin_lib"}


def build_test_helpers(project_root: Path, verbose: bool = False) -> bool:
    """Build test helper libraries. Returns True on success."""
    helpers_dir = project_root / "tests" / "libs" / "helpers"
    bin_dir = project_root / "tests" / "libs" / "bin"
    sushic = project_root / "sushic"

    if not helpers_dir.exists():
        if verbose:
            print("No test helpers directory found, skipping...")
        return True

    helper_files = list(helpers_dir.glob("*.sushi"))
    # A SUBDIRECTORY is one MULTI-UNIT library: its entry point is the .sushi
    # file that carries the directory's name, and the other .sushi files beside
    # it are the units the entry imports (PL P0).
    helper_files += [d / f"{d.name}.sushi" for d in helpers_dir.iterdir()
                     if d.is_dir() and (d / f"{d.name}.sushi").exists()]
    if not helper_files:
        if verbose:
            print("No test helper libraries found, skipping...")
        return True

    if verbose:
        print("Building test helper libraries...")

    bin_dir.mkdir(parents=True, exist_ok=True)

    for lib_file in helper_files:
        name = lib_file.stem
        output_path = bin_dir / f"{name}.slib"

        if verbose:
            print(f"  Compiling {name}...")

        try:
            result = subprocess.run(
                # A .slib must state its own version (CE3505). These helpers are not
                # packages, so there is no nori.toml to read one from.
                [str(sushic), "--lib", "--lib-version", "0.0.0",
                 "--lib-kind", "binary" if name in BINARY_ONLY_HELPERS else "source",
                 str(lib_file), "-o", str(output_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                print(f"Failed to compile {name}: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"Compilation of {name} timed out")
            return False
        except Exception as e:
            print(f"Error compiling {name}: {e}")
            return False

    if verbose:
        print(f"  Libraries compiled to {bin_dir}")

    return True


DOC_GATE_MODULES_ENV = "SUSHI_DOC_GATE_MODULES"
# `path[:line:col]: warning|error [CODE]: ...` -- the head line of one diagnostic.
_DIAGNOSTIC_HEAD = re.compile(
    r"^(?P<path>[^:\s][^:]*?)(?::\d+:\d+)?: (?:warning|error) \[(?P<code>C[EW]\d{4})\]")
_DOC_CODE = re.compile(r"^C[EW]70\d\d$")
# A synthetic module of the runner tests is not in the registry, so the compiler must be
# told about it. This bootstrap adds it to the registry of ONE compiler process and runs
# that compiler; only the SUSHI_DOC_GATE_MODULES override uses it.
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
    """What the stdlib doc-block gate found, or why it did not run."""
    modules: List[str] = field(default_factory=list)
    failures: Dict[str, List[str]] = field(default_factory=dict)
    skip_reason: Optional[str] = None
    duration: float = 0.0

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
            return [f"Stdlib doc-block gate SKIPPED: {self.skip_reason}."]
        lines = [f"Stdlib doc-block gate: {len(self.modules)} module(s), "
                 f"{len(self.failures)} failed ({self.duration:.2f}s)"]
        for name, findings in sorted(self.failures.items()):
            lines.append(f"  {name}: {len(findings)} finding(s)")
            lines.extend(f"    {f}" for f in findings)
        return lines


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

    def spelled(path: Path) -> str:
        try:
            return path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    chosen = {name: path for name, path in modules.items()
              if filter_pattern in name or filter_pattern in spelled(path)}
    if not chosen:
        return {}, f"--filter {filter_pattern!r} selects no stdlib module"
    return chosen, None


def doc_gate_program(modules: Dict[str, Path]) -> str:
    """ONE program that imports every module, each behind an alias so no bare name clashes."""
    uses = [f"use <{name}> as dg_{name.replace('/', '_')}" for name in sorted(modules)]
    return "\n".join(["##: Imports every module the stdlib doc-block gate checks. :##", "",
                      *uses, "", "fn main() i32:", "    return Result.Ok(0)", ""])


def _doc_findings(output: str, cwd: Path,
                  modules: Dict[str, Path]) -> Dict[str, List[str]]:
    """Every doc-pass diagnostic whose location is one of the checked module files.

    The compiler prints a location relative to its working directory, so `cwd` must hold
    every module file; outside it the compiler prints the bare file name.
    """
    owner = {path.resolve(): name for name, path in modules.items()}
    found: Dict[str, List[str]] = {}
    for line in output.splitlines():
        head = _DIAGNOSTIC_HEAD.match(line.strip())
        if head is None or not _DOC_CODE.match(head["code"]):
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
            command = [sys.executable, "-c", _OVERRIDE_BOOTSTRAP,
                       json.dumps({n: str(p) for n, p in chosen.items()}), *flags]
            cwd = Path(os.path.commonpath([p.parent for p in chosen.values()]))
            env["PYTHONPATH"] = os.pathsep.join(
                [str(project_root), *filter(None, [env.get("PYTHONPATH")])])
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


def purge_unit_caches(project_root: Path, verbose: bool = False) -> None:
    """Delete every tests/**/__sushi_cache__ before a run."""
    for cache in (project_root / "tests").rglob("__sushi_cache__"):
        if cache.is_dir():
            if verbose:
                print(f"  Removing stale cache: {cache}")
            shutil.rmtree(cache, ignore_errors=True)


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



def main():
    """The front end: build what a run needs, then hand it to the ONE runner.

    `run_tests.py` used to carry a second runner of its own that compiled every fixture
    and checked the compiler's exit status alone -- it read no `EXPECT_*` directive, so a
    `test_err_` fixture passed it whatever diagnostic the compiler printed (#760). It was
    not the compile HALF of the suite; it was the whole suite with the assertions turned
    off, and it bought no speed for them: over `tests/diagnostics/`, where almost nothing
    runs a binary, it measured 6.95s against the enhanced runner's 6.85s.

    What remains here is what both halves always shared -- the stdlib and helper builds,
    the leak interposer, the cache purge and the spelling gate -- plus the argument
    parsing. `enhanced_test_runner` runs the tests.
    """
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

    args = parser.parse_args()

    arm_spelling_gate()

    # A warm cache can outlive a codegen change (see purge_unit_caches).
    purge_unit_caches(Path(__file__).parent.parent, verbose=args.verbose)

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
    return enhanced_test_runner.main()


if __name__ == "__main__":
    sys.exit(main())
