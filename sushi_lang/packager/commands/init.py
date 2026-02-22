"""nori init - generate a template nori.toml manifest."""
import argparse
from pathlib import Path

from sushi_lang.packager.constants import MANIFEST_NAME

TEMPLATE = """\
[package]
name = "{name}"
version = "0.1.0"
description = ""
author = ""
license = ""

[files]
libraries = []
executables = []
data = []

[dependencies]
"""


def cmd_init(args: argparse.Namespace) -> int:
    manifest_path = Path.cwd() / MANIFEST_NAME
    if manifest_path.exists():
        print(f"{MANIFEST_NAME} already exists in this directory.")
        return 1

    # Derive default package name from directory name
    dir_name = Path.cwd().name.lower().replace("_", "-").replace(" ", "-")
    content = TEMPLATE.format(name=dir_name)
    manifest_path.write_text(content)
    print(f"Created {MANIFEST_NAME}")
    return 0
