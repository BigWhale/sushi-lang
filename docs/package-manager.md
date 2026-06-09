# Nori Package Manager

[← Back to Documentation](index.md)

Nori is the package manager for Sushi Lang. It handles packaging, installing, and managing precompiled Sushi libraries and executables. Nori ships alongside the compiler in the same distribution.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [The Manifest](#the-manifest)
- [Building Packages](#building-packages)
- [Installing Packages](#installing-packages)
- [Project Environments](#project-environments)
- [Searching for Packages](#searching-for-packages)
- [Managing Packages](#managing-packages)
- [Compiler Integration](#compiler-integration)
- [Package Directory Structure](#package-directory-structure)
- [Archive Format](#archive-format)
- [Workflow Example](#workflow-example)
- [Command Reference](#command-reference)
- [Limitations](#limitations)

## Overview

Nori fills the gap between compiling Sushi libraries and distributing them. The compiler produces `.slib` files, but getting third-party libraries into the right directories is entirely manual without a package manager.

Nori provides:

- **Packaging**: Bundle `.slib` libraries, executables, and data files into a distributable `.nori` archive
- **Installation**: Extract and install packages to `~/.sushi/bento/`
- **Discovery**: The compiler automatically finds libraries installed by Nori
- **Management**: List, inspect, and remove installed packages

**Important**: Nori does not compile Sushi source code. Compile with `sushic` first, then use Nori to package and distribute the outputs.

## Getting Started

Nori is included with the Sushi Lang installation. Verify it works:

```bash
nori --version
```

For development from the repository:

```bash
./nori --version
```

## The Manifest

Every Nori package requires a `nori.toml` manifest file describing the package and its contents.

### Creating a Manifest

Generate a template manifest in the current directory:

```bash
nori init
```

This creates a `nori.toml` with the package name derived from the directory name.

### Manifest Format

```toml
[package]
name = "my-package"
version = "1.0.0"
description = "A useful Sushi package"
author = "Author Name"
license = "Apache-2.0"

[files]
libraries = ["build/mylib.slib"]     # .slib files
executables = ["build/mytool"]       # compiled binaries
data = ["data/config.toml"]          # any other files

[dependencies]
math-utils = "1.0.0"
text-tools = "0.2.1"
```

The `[dependencies]` section tracks project-local package versions. Nori populates it automatically when you install packages inside a project directory.

### Package Section

| Field         | Required | Description                    |
|---------------|----------|--------------------------------|
| `name`        | Yes      | Package name (see naming rules)|
| `version`     | Yes      | Semantic version (`1.0.0`)     |
| `description` | No       | Short package description      |
| `author`      | No       | Author name                    |
| `license`     | No       | License identifier             |

### Files Section

| Field         | Description                                    |
|---------------|------------------------------------------------|
| `libraries`   | Paths to `.slib` files to include              |
| `executables` | Paths to compiled binaries (permissions preserved) |
| `data`        | Paths to data files or directories             |

All paths are relative to the directory containing `nori.toml`.

### Naming Rules

Package names must:
- Start with a lowercase letter
- Contain only lowercase letters, digits, and hyphens
- Be between 1 and 64 characters

```
my-package      # valid
sushi-utils     # valid
MyPackage       # invalid (uppercase)
123-lib         # invalid (starts with digit)
```

### Version Format

Versions must follow `major.minor.patch` format:

```
1.0.0           # valid
0.1.0           # valid
2.10.3          # valid
1.0             # invalid (missing patch)
```

## Building Packages

Once you have a `nori.toml` and the referenced files exist, build a package archive:

```bash
nori build
```

This creates a `.nori` archive in the `dist/` directory:

```
Built my-package v1.0.0
  3 file(s), 12.4 KB
  dist/my-package-1.0.0.nori
```

The build command validates that all files listed in the manifest exist before creating the archive.

## Installing Packages

### From a `.nori` Archive

Install directly from an archive file:

```bash
nori install ./dist/my-package-1.0.0.nori
```

### From a Directory Containing Archives

Use the `from` syntax to search a directory for a matching archive:

```bash
nori install my-package from ./dist/
```

This finds the archive matching the package name in the specified directory. If multiple versions exist, the latest (by name sort) is used.

### From a Source Directory

If the source directory contains a `nori.toml` instead of archives, Nori installs directly from the source:

```bash
nori install my-package from ./my-package-src/
```

### What Happens During Installation

**In a project directory** (containing `nori.toml`):

1. The archive is cached in `~/.sushi/cache/`
2. Contents are extracted to `~/.sushi/store/{name}-{version}/`
3. A symlink is created at `.sushi_bento/{name}-{version}/` in the project root
4. `nori.toml` `[dependencies]` is updated with the package version
5. Executables are symlinked to `~/.sushi/bin/`

**Outside a project** (or with `--global`):

1. The archive is cached in `~/.sushi/cache/`
2. Contents are extracted to `~/.sushi/bento/{package-name}/`
3. Executables are symlinked to `~/.sushi/bin/`
4. A PATH hint is printed for executable access

Re-installing a package replaces the existing installation.

### Remote Sources

Remote package installation (HTTP URLs, Omakase repository) is planned for a future release.

## Project Environments

Nori supports project-local dependency management modeled after Go modules and Cargo. When you run `nori install` inside a directory containing a `nori.toml`, Nori treats it as a project context and installs packages locally rather than globally.

### How It Works

Packages have two storage locations:

- **Global store**: `~/.sushi/store/{name}-{version}/` — versioned, immutable copies shared across all projects
- **Project-local**: `.sushi_bento/` — symlinks from the project root into the global store

When you install a package inside a project:

1. The package is extracted to `~/.sushi/store/{name}-{version}/`
2. A symlink is created at `.sushi_bento/{name}-{version}/`
3. The `[dependencies]` section of `nori.toml` is updated with the package version

### Project Detection

Nori detects project context by walking up from the current directory looking for a `nori.toml`. If found, you are in a project context and installs are project-local by default.

### Installing Packages in a Project

```bash
# Inside a project (nori.toml exists here or in a parent directory)
nori install math-utils-1.0.0.nori      # installs to store + symlinks .sushi_bento/
nori install math-utils from ./dist/    # same behavior from a directory source
```

### Restoring All Dependencies

To install all packages listed in `[dependencies]` (e.g., after cloning a project):

```bash
nori install
```

This reads every entry from `[dependencies]` in `nori.toml` and installs any that are not already present in the store.

### Forcing a Global Install

To install globally even when inside a project:

```bash
nori install --global math-utils-1.0.0.nori
```

This installs to `~/.sushi/bento/` and does not update `nori.toml`.

### Committing `.sushi_bento/`

`.sushi_bento/` contains only symlinks and is safe to add to `.gitignore`. The `nori.toml` `[dependencies]` section is what should be committed — teammates restore the environment with `nori install`.

## Searching for Packages

Search for packages in the [Omakase](https://omakase.lubica.net) remote repository:

```bash
nori search <query>
```

Example:

```bash
nori search math
```

Output:

```
Name                           Version      Description
------------------------------ ------------ ------------------------------
math-utils                     1.0.0        Math utility library for Sushi
fast-math                      0.3.0        SIMD-accelerated math routines

2 result(s) found.
```

The search queries the Omakase API and returns matching packages by name and description. Results are sorted by relevance. Use pagination flags for large result sets:

```bash
nori search math --page 2
nori search math --limit 20
```

## Managing Packages

### Listing Installed Packages

```bash
nori list           # project-local packages (if in a project), else global
nori list --global  # globally installed packages in ~/.sushi/bento/
```

Output:

```
Package                        Version      Description
------------------------------ ------------ ------------------------------
math-utils                     1.0.0        Math utility library
text-tools                     0.2.1        Text processing tools

2 package(s) installed.
```

### Viewing Package Details

```bash
nori info math-utils
```

Output:

```
Package:     math-utils
Version:     1.0.0
Description: Math utility library
Author:      Jane Doe
License:     Apache-2.0
Location:    /home/user/.sushi/bento/math-utils
Files:
  lib/mathutils.slib
  bin/mathcalc
  data/constants.toml
```

### Removing Packages

```bash
nori remove math-utils           # removes from project (.sushi_bento/) and updates nori.toml
nori remove --global math-utils  # removes from ~/.sushi/bento/
```

Project removal removes:
- The symlink from `.sushi_bento/`
- The entry from `nori.toml` `[dependencies]`

Global removal removes:
- The package directory from `~/.sushi/bento/`
- Executable symlinks from `~/.sushi/bin/`
- Cached archives from `~/.sushi/cache/`

## Compiler Integration

Libraries installed by Nori are automatically found by the compiler. No manual `SUSHI_LIB_PATH` configuration is needed.

### Search Order

The compiler searches for `.slib` files in this order:

1. Directories in `SUSHI_LIB_PATH` (if set)
2. Project-local packages (`.sushi_bento/*/lib/`)
3. Global Nori packages (`~/.sushi/bento/*/lib/`)
4. Current working directory

### Example

After installing a package containing `mathutils.slib`:

```sushi
# No SUSHI_LIB_PATH needed - the compiler finds it automatically
use <lib/mathutils>

fn main() i32:
    let i32 result = add(10, 20).realise(0)
    println("{result}")
    return Result.Ok(0)
```

## Package Directory Structure

Nori uses two storage locations: a global store under `~/.sushi/` and a project-local `.sushi_bento/` directory.

### Global store

```
~/.sushi/
    bin/                                    # executable symlinks
        mytool -> ../store/my-package-1.0.0/bin/mytool
    cache/                                  # downloaded .nori archives
        my-package-1.0.0.nori
    store/
        my-package-1.0.0/                   # versioned package copy
            nori.toml
            lib/
                mylib.slib
            bin/
                mytool
            data/
                config.toml
    bento/
        my-package/                         # global installs (no project context)
            nori.toml
            lib/
                mylib.slib
```

### Project-local

```
my-project/
    nori.toml                               # manifest with [dependencies]
    .sushi_bento/
        my-package-1.0.0 -> ~/.sushi/store/my-package-1.0.0/
```

| Location | Purpose |
|----------|---------|
| `~/.sushi/store/` | Versioned, immutable package copies shared across projects |
| `~/.sushi/bento/` | Packages installed globally (outside any project) |
| `~/.sushi/bin/`   | Symlinks to package executables, add to `PATH` for access |
| `~/.sushi/cache/` | Cached `.nori` archives from installations |
| `.sushi_bento/`   | Per-project symlinks into the global store |

## Archive Format

`.nori` files are gzip-compressed tarballs. The internal structure uses a version-prefixed directory:

```
my-package-1.0.0/
    nori.toml
    lib/
        mylib.slib
    bin/
        mytool
    data/
        config.toml
```

Archives can be inspected with standard tools:

```bash
tar tzf my-package-1.0.0.nori
```

## Workflow Example

A complete workflow for creating and distributing a Sushi library:

```bash
# 1. Write your library
cat > mathlib.sushi << 'EOF'
public fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

public fn multiply(i32 a, i32 b) i32:
    return Result.Ok(a * b)
EOF

# 2. Compile to .slib
./sushic --lib mathlib.sushi -o build/mathlib.slib

# 3. Create manifest
nori init
# Edit nori.toml to add: libraries = ["build/mathlib.slib"]

# 4. Build the package
nori build

# 5. Install locally
nori install ./dist/math-lib-1.0.0.nori

# 6. Verify
nori list
nori info math-lib

# 7. Use in a program (compiler finds it automatically)
./sushic program.sushi -o program
```

## Command Reference

| Command | Description |
|---------|-------------|
| `nori --version` | Show version information |
| `nori init` | Create a template `nori.toml` in the current directory |
| `nori build` | Build a `.nori` archive from the current directory's manifest |
| `nori install` | Restore all dependencies listed in `nori.toml` (project context) |
| `nori install <archive>` | Install from a `.nori` file (project-local if in project, else global) |
| `nori install <name> from <path>` | Install from a directory or archive source |
| `nori install --global <archive>` | Force global install, skip `nori.toml` update |
| `nori search <query>` | Search Omakase for packages matching the query |
| `nori search <query> --page <n>` | Paginate search results |
| `nori search <query> --limit <n>` | Set results per page |
| `nori list` | List project-local packages (or global if outside a project) |
| `nori list --global` | List globally installed packages |
| `nori info <name>` | Show details about an installed package |
| `nori remove <name>` | Remove package from project and update `nori.toml` |
| `nori remove --global <name>` | Remove globally installed package |

## Limitations

1. **No build step**: Nori does not compile Sushi source. Use `sushic` to compile first.
2. **Local sources only**: Remote installation via `nori install` (HTTP, Omakase) is not yet available. Use `nori search` to find packages, then install from local archives.
3. **No version constraint syntax**: `[dependencies]` records exact versions only; range specifiers (`^1.0`, `>=0.2`) are not supported.
4. **Platform-specific**: `.slib` files are not portable across platforms (same as the compiler).
5. **No transitive dependency resolution**: If package A depends on package B, you must install both explicitly.

## See Also

- [Libraries](libraries.md) - Creating and linking Sushi libraries
- [Library Format](library-format.md) - `.slib` binary format specification
- [Compiler Reference](compiler-reference.md) - All compiler CLI options
- [Getting Started](getting-started.md) - Introduction to Sushi
