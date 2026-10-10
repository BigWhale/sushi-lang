"""The build settings the pipeline reads, taken once from the parsed command line."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.semantic_analyzer import Lints


@dataclass(frozen=True)
class BuildOptions:
    """What one compilation was asked to do. The pipeline never sees the `Namespace`."""
    out: Optional[str]
    opt: str
    no_verify: bool
    keep_object: bool
    ignore_compiler_version: bool
    warn_missing_docs: bool
    warn_unused: bool
    lib: bool
    lib_kind: str
    lib_version: Optional[str]
    write_ll: bool
    dump_ll: bool
    no_incremental: bool
    cache_dir: Optional[str]
    dont_panic: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "BuildOptions":
        return cls(
            out=args.out,
            opt=args.opt,
            no_verify=args.no_verify,
            keep_object=args.keep_object,
            ignore_compiler_version=args.ignore_compiler_version,
            warn_missing_docs=args.warn_missing_docs,
            warn_unused=args.warn_unused,
            lib=args.lib,
            lib_kind=args.lib_kind,
            lib_version=args.lib_version,
            write_ll=args.write_ll,
            dump_ll=args.dump_ll,
            no_incremental=args.no_incremental,
            cache_dir=args.cache_dir,
            dont_panic=args.dont_panic,
        )

    @property
    def lints(self) -> "Lints":
        """The warning-control flags, as the one object the analyzer takes."""
        from sushi_lang.semantics.semantic_analyzer import Lints
        return Lints(missing_docs=self.warn_missing_docs, unused=self.warn_unused)
