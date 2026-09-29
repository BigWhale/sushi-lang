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
- [Publishing Packages](#publishing-packages)
- [Compiler Integration](#compiler-integration)
- [Package Directory Structure](#package-directory-structure)
- [Archive Format](#archive-format)
- [Workflow Example](#workflow-example)
- [Command Reference](#command-reference)
- [Errors and Exit Codes](#errors-and-exit-codes)
- [Limitations](#limitations)

## Overview

Nori fills the gap between compiling Sushi libraries and distributing them. The compiler produces `.slib` files, but getting third-party libraries into the right directories is entirely manual without a package manager.

Nori provides:

- **Packaging**: Bundle `.slib` libraries, executables, and data files into a distributable `.nori` archive
- **Installation**: Extract and install packages to `~/.sushi/bento/`
- **Discovery**: The compiler automatically finds libraries installed by Nori
- **Management**: List, inspect, and remove installed packages
- **Publishing**: Upload a package to the [Omakase](https://omakase.lubica.net) repository

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

All paths are relative to the directory containing `nori.toml`, and each one stays at
or below that directory. `nori build` refuses an absolute path or a path with a `..` step
(**NE2008**), so an archive never packs a file from outside the package.

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

Nori cannot install a package from a remote repository yet. `nori install <name>` with no
`from <path>` stops with **NE3009** (`remote install from <repository> is not implemented
yet`). Install from a local archive or directory instead.

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

A project is the directory that you run the tool in. When the current directory holds a
`nori.toml`, you are in a project context and installs are project-local by default. Nori
does not look in a parent directory: in a subdirectory of a project, you are not in that
project.

The same rule applies to every tool:

- `nori install`, `nori list` and `nori remove` use the `nori.toml` and the `.sushi_bento/`
  of the current directory. `nori build`, `nori publish` and `nori init` also read
  `./nori.toml` only.
- A program compile finds the project's `.sushi_bento/` packages only when `sushic` runs in
  the project root.
- `sushic --lib` reads `[package] version` from the `nori.toml` in the current directory,
  not from one beside the sources. Thus `sushic --lib src/mathlib.sushi -o
  build/mathlib.slib`, run in the package root, reads the root's `nori.toml`. A
  `nori.toml` that exists must be valid: a file that cannot be read is **CE3518**, and any
  fault that nori refuses (for example a bad package name, NE1005) is **CE3517**. With no
  `nori.toml` in the current directory, the build needs `--lib-version` (**CE3505**).

### Installing Packages in a Project

```bash
# In the project root (the current directory holds nori.toml)
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

The search queries the Omakase API and returns matching packages by name and description.
These options change the search:

| Option | Default | Description |
|--------|---------|-------------|
| `--repository URL` | `omakase.lubica.net` | The repository to search |
| `--namespace {stable,testing}` | `stable` | The namespace to search |
| `--platform {darwin,linux,windows,any}` | none | Show only the packages for this platform |
| `--sort {relevance,name,downloads,updated}` | `relevance` | The sort order |
| `--page N` | `1` | The page of results |
| `--per-page N` | `20` | The number of results on a page |

```bash
nori search math --page 2
nori search math --sort downloads --namespace testing
```

The repository comes from `--repository`, then from the environment variable
`SUSHI_REPOSITORY`, then from the default `omakase.lubica.net`. The same rule applies to
`install`, `publish`, `login` and `status`.

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

## Publishing Packages

To publish a package to an Omakase repository, log in once, build the archive, and publish
it from the package root.

### Logging In

```bash
nori login
```

Nori reads the API key from the terminal (or from standard input), never from the command
line, so the key does not go into the shell history. A key starts with `nori_`; another
key is **NE5005**. Nori verifies the key with the repository and keeps it in
`~/.sushi/credentials.toml` (mode `0600`), one key for each repository.

### Checking the Login

```bash
nori status
```

This prints the repository, your user name and the packages that you published. When you
are not logged in, it says so and tells you to run `nori login`.

### Publishing

```bash
nori build
nori publish
```

`nori publish` reads `./nori.toml` and uploads `dist/<name>-<version>.nori` with its
manifest. The archive must exist (run `nori build` first, else **NE2007**), and you must be
logged in (else **NE5003**).

| Option | Default | Description |
|--------|---------|-------------|
| `--repository URL` | `omakase.lubica.net` | The repository to publish to |
| `--namespace {stable,testing}` | `stable` | The namespace of the package |
| `--platform {darwin,linux,windows,any}` | the current operating system (`darwin`, `linux`, else `any`) | The platform of the package |

The repository can refuse a publish: a key that the repository refuses (invalid, expired or revoked) is **NE5004**, a package that another
user owns is **NE5006**, a version that is already published is **NE5007**, an archive that
is too large is **NE5008**, and a package that the repository does not accept is
**NE5009**.

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

<!-- docs-sweep: skip (needs a .slib library built from the page's earlier example) -->
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

A complete workflow for creating and distributing a Sushi library, in a directory named
`math-lib`:

```bash
# 0. Make the package directory
mkdir math-lib && cd math-lib
mkdir build                  # sushic does not create the -o directory (CE3019)

# 1. Write your library
cat > mathlib.sushi << 'EOF'
public fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

public fn multiply(i32 a, i32 b) i32:
    return Result.Ok(a * b)
EOF

# 2. Compile to .slib (--lib-version, because there is no nori.toml yet)
./sushic --lib --lib-version 1.0.0 mathlib.sushi -o build/mathlib.slib

# 3. Create manifest (the package name comes from the directory name, here math-lib)
nori init
# Edit nori.toml: set version = "1.0.0" (nori init writes 0.1.0),
# and add libraries = ["build/mathlib.slib"]

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
| `nori help` | Show the usage text |
| `nori init` | Create a template `nori.toml` in the current directory |
| `nori build` | Build a `.nori` archive from the current directory's manifest |
| `nori install` | Restore all dependencies listed in `nori.toml` (project context) |
| `nori install <archive>` | Install from a `.nori` file (project-local if in project, else global) |
| `nori install <name> from <path>` | Install from a directory or archive source |
| `nori install --global <archive>` | Force global install, skip `nori.toml` update |
| `nori search <query>` | Search Omakase for packages matching the query |
| `nori search <query> --page <n>` | Paginate search results |
| `nori search <query> --per-page <n>` | Set results per page |
| `nori list` | List project-local packages (or global if outside a project) |
| `nori list --global` | List globally installed packages |
| `nori info <name>` | Show details about an installed package |
| `nori remove <name>` | Remove package from project and update `nori.toml` |
| `nori remove --global <name>` | Remove globally installed package |
| `nori publish` | Publish `dist/<name>-<version>.nori` to a repository |
| `nori login` | Store an API key for a repository |
| `nori status` | Show the login and the published packages |

`install`, `search`, `publish`, `login` and `status` take `--repository URL`. `search` and
`publish` also take `--namespace` and `--platform`; see [Searching for
Packages](#searching-for-packages) and [Publishing Packages](#publishing-packages).

These global options come before the command:

| Option | Description |
|--------|-------------|
| `--color {auto,always,never}` | When to use ANSI colour. `auto` (the default) reads `NO_COLOR`, `CLICOLOR_FORCE`, `TERM` and whether the stream is a terminal, with the same rules as `sushic` |
| `--traceback` | Append the Python traceback to an error (for debugging) |

## Errors and Exit Codes

A nori error has a code in the **NExxxx** family and names the file that it is about. The
ranges are: NE00xx internal, NE10xx the manifest, NE20xx the archive, NE30xx the installed
packages, NE40xx the operating system, NE50xx the package repository. The text of each code
is in `sushi_lang/internals/errors/nori.py`.

nori exits 0 on success, 1 for a user fault (an NExxxx error), and 2 for an internal error
(**NE0000**).

## Limitations

1. **No build step**: Nori does not compile Sushi source. Use `sushic` to compile first.
2. **Local sources only**: `nori install` cannot install from a remote repository (NE3009). Use `nori search` to find packages, then install from local archives.
3. **No version constraint syntax**: `[dependencies]` records exact versions only; range specifiers (`^1.0`, `>=0.2`) are not supported.
4. **Platform-specific binary libraries**: a `binary` or `hybrid` `.slib` is bound to the platform that built it. The default `source` kind is portable.
5. **No transitive dependency resolution**: If package A depends on package B, you must install both explicitly.

## See Also

- [Libraries](libraries.md) - Creating and linking Sushi libraries
- [Library Format](library-format.md) - `.slib` file format specification
- [Compiler Reference](compiler-reference.md) - All compiler CLI options
- [Getting Started](getting-started.md) - Introduction to Sushi
