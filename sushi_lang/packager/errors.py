"""nori failures: one exception that carries an NE code, rendered through the one report channel."""
from __future__ import annotations

import os
import sys
import tomllib
from typing import IO, Iterable, Optional

from sushi_lang.internals.errors import message_for
from sushi_lang.internals.report import Reporter

# The diagnostic location of a failure that is nori's and names no source position.
NORI_LOCATION = None

RELOGIN_HELP = "run 'nori login' to authenticate again"


class NoriError(Exception):
    """A failure that `cli_main` renders as `error [NExxxx]` and answers with exit 1."""

    def __init__(self, code: str, *, helps: Iterable[str] = (), **params: object) -> None:
        self.code = code
        self.params = params
        self.helps = list(helps)
        super().__init__(code)

    def __str__(self) -> str:
        return message_for(self.code, **self.params)

    def recast(self, cls: type[NoriError], **params: object) -> NoriError:
        """The same failure as a `cls`, with `params` replacing its own."""
        return cls(self.code, helps=self.helps, **{**self.params, **params})


def toml_error(path: object, exc: tomllib.TOMLDecodeError) -> NoriError:
    """NE1002 for a TOML file nori reads; the detail names the line and the column."""
    lineno, colno = getattr(exc, "lineno", None), getattr(exc, "colno", None)
    detail = str(exc) if lineno is None else f"{getattr(exc, 'msg', exc)} (line {lineno}, column {colno})"
    return NoriError("NE1002", path=path, detail=detail)


def load_toml(path: os.PathLike[str]) -> dict:
    """Read a TOML file; a malformed one is NE1002, an unreadable one an OSError."""
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise toml_error(path, e) from e


def from_os_error(exc: OSError) -> NoriError:
    """The environment refused an operation: the path and the system reason, never the type."""
    reason = exc.strerror or str(exc) or "no reason given"
    if exc.filename is not None:
        return NoriError("NE4001", path=os.fsdecode(exc.filename), reason=reason)
    return NoriError("NE4002", reason=reason)


def report(err: NoriError, notes: Iterable[str] = (), stream: Optional[IO[str]] = None) -> None:
    reporter = Reporter(filename=NORI_LOCATION)
    builder = reporter.error_with(err.code, str(err), None)
    for note in notes:
        builder.note(note)
    for help_text in err.helps:
        builder.help(help_text)
    reporter.print(stream or sys.stderr)


def report_internal(exc: BaseException, show_help: bool) -> None:
    """NE0000: a fault in nori itself."""
    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    helps = (["re-run with --traceback for the full Python traceback, then please report it"]
             if show_help else [])
    report(NoriError("NE0000", helps=helps, detail=detail),
           notes=["this is a bug in nori, not in your package"])
