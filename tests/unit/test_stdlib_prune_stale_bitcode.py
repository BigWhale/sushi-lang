"""A stdlib build deletes the `.bc` files that no row wrote (#1324).

The linker finds the stdlib units on disk, so a stale `.bc` of a removed row links again.
"""
from __future__ import annotations

from sushi_lang.sushi_stdlib.build import prune_stale_bitcode


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_prune_deletes_only_the_bitcode_that_no_row_wrote(tmp_path):
    platform_dir = tmp_path / "darwin"
    for name in ("io/files.bc", "core/primitives.bc", "random.bc", "sys/env.bc",
                 "sys/process.bc", "symbols.json", ".build_fingerprint", "net/notes.txt"):
        _touch(platform_dir / name)

    prune_stale_bitcode(platform_dir, {"io/files.bc", "sys/process.bc"})

    remaining = sorted(p.relative_to(platform_dir).as_posix()
                       for p in platform_dir.rglob("*"))
    assert remaining == [
        ".build_fingerprint", "io", "io/files.bc", "net", "net/notes.txt",
        "symbols.json", "sys", "sys/process.bc",
    ]


def test_prune_of_a_missing_directory_does_nothing(tmp_path):
    prune_stale_bitcode(tmp_path / "linux", {"io/files.bc"})
    assert not (tmp_path / "linux").exists()
