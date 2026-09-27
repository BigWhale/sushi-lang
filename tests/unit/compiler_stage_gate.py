"""The gate that no pytest test outside the runner set reaches a compiler stage (#959).

pytest never runs the Sushi compiler: the fixture corpus under `tests/<area>/` is the one
place that tests it. The one exception is a test of the test runner, and those live under
`tests/unit/runner/`. `conftest.py` installs this gate for the whole unit layer.

A stage is a Sushi parse or lex, the semantic analysis, the code generator, the driver, or
a subprocess that starts the compiler, the runner or the toolchain build. A hit is recorded
against the item that runs, or against the fixture that is being set up (then every item
that uses that fixture fails), or against the module that is being collected. The hit does
not stop the stage; the item fails when pytest writes its report.
"""
from __future__ import annotations

import functools
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import pytest

RUNNER_SET = "runner"

_SUBPROCESS_MARKERS = ("run_tests.py", "enhanced_test_runner", "toolchain/build.py")


def compiler_argument(args: object) -> Optional[str]:
    """The argument that makes a subprocess command a compiler run, or None."""
    if isinstance(args, (str, bytes)):
        tokens = os.fsdecode(args).split()
    elif isinstance(args, os.PathLike):
        tokens = [os.fsdecode(args)]
    else:
        try:
            tokens = [os.fsdecode(a) if isinstance(a, (str, bytes, os.PathLike)) else str(a)
                      for a in args]  # type: ignore[union-attr]
        except TypeError:
            return None
    for index, token in enumerate(tokens):
        name = token.rstrip("/").rsplit("/", 1)[-1]
        if name == "sushic" or token.endswith(".sushi"):
            return token
        if any(marker in token for marker in _SUBPROCESS_MARKERS):
            return token
        if token == "-m" and index + 1 < len(tokens):
            module = tokens[index + 1]
            if module == "sushi_lang" or module.startswith("sushi_lang.compiler"):
                return f"-m {module}"
    return None


@dataclass
class _Context:
    item: Optional[pytest.Item] = None
    fixtures: List[Tuple[str, str]] = field(default_factory=list)
    collector: Optional[pytest.Collector] = None


class CompilerStageGate:
    """The wrappers, the context they read, and the hits they record."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._context = _Context()
        self._lock = threading.Lock()
        self._restore: List[Callable[[], None]] = []
        self.item_hits: Dict[str, List[str]] = {}
        self.fixture_hits: Dict[Tuple[str, str], List[str]] = {}
        self.collector_hits: Dict[str, List[str]] = {}
        self.unattributed: List[str] = []
        self._reported: set = set()

    # -- the stages -----------------------------------------------------------

    def install(self) -> None:
        import subprocess

        import lark

        from sushi_lang.backend.codegen_llvm import LLVMCodegen
        from sushi_lang.backend.driver import LLVMDriver
        from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer

        self._wrap(lark.Lark, "parse", "lark.Lark.parse")
        self._wrap(lark.Lark, "lex", "lark.Lark.lex")
        self._wrap(SemanticAnalyzer, "check", "SemanticAnalyzer.check")
        self._wrap(LLVMCodegen, "__init__", "LLVMCodegen")
        self._wrap(LLVMDriver, "__init__", "LLVMDriver")
        self._wrap(subprocess.Popen, "__init__", "subprocess",
                   when=lambda args, kwargs: compiler_argument(
                       kwargs.get("args", args[0] if args else None)))

    def uninstall(self) -> None:
        while self._restore:
            self._restore.pop()()

    def _wrap(self, owner: type, name: str, stage: str,
              when: Optional[Callable[[tuple, dict], Optional[str]]] = None) -> None:
        original = owner.__dict__[name]
        gate = self

        @functools.wraps(original)
        def wrapper(obj, *args, **kwargs):
            if when is None:
                gate.hit(stage)
            else:
                argument = when(args, kwargs)
                if argument is not None:
                    gate.hit(f"{stage} ({argument})")
            return original(obj, *args, **kwargs)

        setattr(owner, name, wrapper)
        self._restore.append(lambda: setattr(owner, name, original))

    # -- the context ----------------------------------------------------------

    def exempt(self, path: Optional[Path]) -> bool:
        """Is `path` inside the runner set?"""
        if path is None:
            return False
        try:
            relative = Path(path).resolve().relative_to(self.root)
        except ValueError:
            return False
        return relative.parts[:1] == (RUNNER_SET,)

    def hit(self, stage: str) -> None:
        with self._lock:
            context = self._context
            if context.fixtures:
                self.fixture_hits.setdefault(context.fixtures[-1], []).append(stage)
            elif context.item is not None:
                self.item_hits.setdefault(context.item.nodeid, []).append(stage)
            elif context.collector is not None:
                self.collector_hits.setdefault(context.collector.nodeid, []).append(stage)
            else:
                self.unattributed.append(stage)

    def running(self, item: Optional[pytest.Item]) -> None:
        self._context.item = item

    def collecting(self, collector: Optional[pytest.Collector]) -> None:
        self._context.collector = collector

    def enter_fixture(self, key: Tuple[str, str]) -> None:
        self._context.fixtures.append(key)

    def leave_fixture(self) -> None:
        self._context.fixtures.pop()

    # -- the verdicts ---------------------------------------------------------

    def hits_of(self, item: pytest.Item) -> List[str]:
        """Every stage this item reached, its fixtures included; empty when exempt."""
        if self.exempt(item.path):
            return []
        found = [f"the test reached {s}" for s in self.item_hits.get(item.nodeid, [])]
        for (argname, baseid), stages in sorted(self.fixture_hits.items()):
            if argname in item.fixturenames and item.nodeid.startswith(baseid):
                found.extend(f"the fixture '{argname}' reached {s}" for s in stages)
        return found

    def unreported_hits_of(self, item: pytest.Item) -> List[str]:
        found = [h for h in self.hits_of(item) if (item.nodeid, h) not in self._reported]
        self._reported.update((item.nodeid, h) for h in found)
        return found

    @staticmethod
    def message(where: str, hits: List[str]) -> str:
        lines = [f"compiler-stage gate: {where} reached a Sushi compiler stage."]
        lines.extend(f"  - {h}" for h in dict.fromkeys(hits))
        lines.append("pytest never runs the Sushi compiler: a fixture under tests/<area>/ "
                     "tests it. Only a test of the test runner, under tests/unit/runner/, "
                     "may start it.")
        return "\n".join(lines)

    # -- the always-fires control ---------------------------------------------

    def recorded_during(self, item: pytest.Item) -> "_Probe":
        return _Probe(self, item)


class _Probe:
    """Take the hits a block records against `item`, and keep them off its report."""

    def __init__(self, gate: CompilerStageGate, item: pytest.Item) -> None:
        self.gate = gate
        self.item = item
        self.stages: List[str] = []

    def __enter__(self) -> "_Probe":
        self._before = len(self.gate.item_hits.get(self.item.nodeid, []))
        return self

    def __exit__(self, *exc: object) -> None:
        hits = self.gate.item_hits.get(self.item.nodeid, [])
        self.stages = hits[self._before:]
        del hits[self._before:]


GATE: Optional[CompilerStageGate] = None


def configure(config: pytest.Config, root: Path) -> None:
    global GATE
    GATE = CompilerStageGate(root)
    GATE.install()
    config.add_cleanup(GATE.uninstall)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Iterator[None]:
    if GATE is None:
        yield
        return
    GATE.collecting(collector)
    outcome = yield
    GATE.collecting(None)
    hits = GATE.collector_hits.pop(collector.nodeid, [])
    if hits and not GATE.exempt(getattr(collector, "path", None)):
        report = outcome.get_result()
        report.outcome = "failed"
        report.longrepr = GATE.message(f"collecting {collector.nodeid}", hits)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    if GATE is None:
        yield
        return
    GATE.running(item)
    try:
        yield
    finally:
        GATE.running(None)


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: pytest.FixtureDef) -> Iterator[None]:
    if GATE is None:
        yield
        return
    GATE.enter_fixture((fixturedef.argname, fixturedef.baseid))
    try:
        yield
    finally:
        GATE.leave_fixture()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    outcome = yield
    if GATE is None:
        return
    hits = GATE.unreported_hits_of(item)
    if hits:
        report = outcome.get_result()
        report.outcome = "failed"
        report.longrepr = GATE.message(item.nodeid, hits)


def pytest_sessionfinish(session: pytest.Session) -> None:
    if GATE is not None and GATE.unattributed:
        session.config.get_terminal_writer().line(
            GATE.message("the session (no test, fixture or module)", GATE.unattributed))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
