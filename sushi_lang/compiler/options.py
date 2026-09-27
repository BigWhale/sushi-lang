"""The build settings the pipeline reads, taken once from the parsed command line."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BuildOptions:
    """What one compilation was asked to do. The pipeline never sees the `Namespace`."""
    out: Optional[str]
    opt: str
    no_verify: bool
    keep_object: bool
    ignore_compiler_version: bool
    warn_missing_docs: bool
    lib: bool
    lib_kind: str
    lib_version: Optional[str]
    write_ll: bool
    dump_ll: bool
    no_incremental: bool
    cache_dir: Optional[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "BuildOptions":
        return cls(
            out=args.out,
            opt=args.opt,
            no_verify=args.no_verify,
            keep_object=args.keep_object,
            ignore_compiler_version=args.ignore_compiler_version,
            warn_missing_docs=args.warn_missing_docs,
            lib=args.lib,
            lib_kind=args.lib_kind,
            lib_version=args.lib_version,
            write_ll=args.write_ll,
            dump_ll=args.dump_ll,
            no_incremental=args.no_incremental,
            cache_dir=args.cache_dir,
        )
