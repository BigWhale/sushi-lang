#!/usr/bin/env python3
"""Comprehensive test runner for Sushi language compiler."""

import argparse
import subprocess
import sys
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

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
