"""The link step: one plan, and one function that makes the `cc` command from it.

This is the ONE place that writes `-l` and `-L` (gate:
`tests/unit/test_link_flags_are_written_by_the_link_plan.py`). Both link paths of the
driver come here. The rules are in `docs/design/libraries.md` and
`docs/adr/0003-one-executable-link-plan.md`.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from sushi_lang.semantics.link_libraries import LinkRequest, library_names


def runtime_libraries() -> tuple[str, ...]:
    """The libraries of the C runtime that every program links, after its own.

    On Linux, libm is part of the runtime: LLVM lowers a float `%` (`frem`) to a call of
    `fmod`, and glibc keeps `fmod` in libm. On macOS, libSystem holds libm.
    """
    from sushi_lang.backend.platform_detect import get_current_platform
    return ("m",) if get_current_platform().is_linux else ()


@dataclass(frozen=True)
class LinkInputs:
    """What the program asks of the link: the requests of its `link` clauses, and the
    directories of `-L`."""
    requests: tuple[LinkRequest, ...] = ()
    search_dirs: tuple[Path, ...] = ()


# A link that asks for no library and no directory.
NO_LINK_INPUTS = LinkInputs()


@dataclass(frozen=True)
class LinkPlan:
    """One link: the objects, the libraries (each one time, in the order of the first
    appearance, then the runtime libraries), the search directories, the output."""
    objects: tuple[Path, ...]
    out: Path
    libraries: tuple[str, ...] = ()
    search_dirs: tuple[Path, ...] = ()
    debug: bool = False
    requests: tuple[LinkRequest, ...] = ()

    @staticmethod
    def of(objects: Sequence[Path], out: Path, debug: bool,
           inputs: LinkInputs = NO_LINK_INPUTS) -> "LinkPlan":
        names = library_names(inputs.requests)
        names += [name for name in runtime_libraries() if name not in names]
        return LinkPlan(objects=tuple(objects), out=out, libraries=tuple(names),
                        search_dirs=inputs.search_dirs, debug=debug,
                        requests=inputs.requests)


def link_command(cc: str, plan: LinkPlan) -> list[str]:
    """The `cc` command line of `plan`: the objects, `-L` for each search directory, then
    `-l` for each library. The order of the libraries matters to GNU ld for a static
    library."""
    cmd = [cc]
    if plan.debug:
        cmd.append("-g")
    cmd += [str(p) for p in plan.objects]
    cmd += ["-o", str(plan.out)]
    cmd += [f"-L{directory}" for directory in plan.search_dirs]
    cmd += [f"-l{name}" for name in plan.libraries]
    return cmd


def run_link(cc: str, plan: LinkPlan) -> None:
    """Run the link step, and report a failure as a diagnostic rather than a crash.

    `check=True` alone let `CalledProcessError` reach the top-level guard, which renders
    any uncaught exception as a CE0000 "this is a bug in the Sushi compiler" -- and printed
    the raw `cc` stderr ahead of it, unfiltered (#251). The output of `cc` is not parsed:
    each library of the plan gets a note at the clause that asked for it.
    """
    from sushi_lang.internals.diagnostics import SushiError

    result = subprocess.run(link_command(cc, plan), capture_output=True, text=True)
    if result.returncode == 0:
        return

    error = SushiError("CE3008", cc=cc, status=result.returncode)
    for stream in (result.stderr, result.stdout):
        for line in (stream or "").splitlines():
            if line.strip():
                error.note(line.rstrip())
    runtime = runtime_libraries()
    for request in plan.requests:
        if request.library in runtime:
            continue
        if request.span is not None:
            error.note_at(f"the library '{request.library}' is asked for here",
                          request.span, request.filename)
    if plan.search_dirs:
        error.note("the directories of -L: "
                   + ", ".join(str(directory) for directory in plan.search_dirs))
    raise error
