# Nori Package Manager

[← Back to Documentation](README.md)

Nori is the package manager for Sushi Lang. It handles packaging, installing, and managing precompiled Sushi libraries and executables. Nori ships alongside the compiler in the same distribution.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [The Manifest](#the-manifest)
- [Building Packages](#building-packages)
- [Installing Packages](#installing-packages)
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
# Future: version-constrained dependencies
```

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

1. The archive is cached in `~/.sushi/cache/`
2. Contents are extracted to `~/.sushi/bento/{package-name}/`
3. Executables are symlinked to `~/.sushi/bin/`
4. A PATH hint is printed for executable access

Re-installing a package replaces the existing installation.

### Remote Sources

Remote package installation (HTTP URLs, Omakase repository) is planned for a future release.

## Managing Packages

### Listing Installed Packages

```bash
nori list
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
nori remove math-utils
```

This removes:
- The package directory from `~/.sushi/bento/`
- Executable symlinks from `~/.sushi/bin/`
- Cached archives from `~/.sushi/cache/`

## Compiler Integration

Libraries installed by Nori are automatically found by the compiler. No manual `SUSHI_LIB_PATH` configuration is needed.

### Search Order

The compiler searches for `.slib` files in this order:

1. Directories in `SUSHI_LIB_PATH` (if set)
2. Nori bento packages (`~/.sushi/bento/*/lib/`)
3. Current working directory

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

All Nori data lives under `~/.sushi/`:

```
~/.sushi/
    bin/                                # executable symlinks
        mytool -> ../bento/my-package/bin/mytool
    cache/                              # downloaded .nori archives
        my-package-1.0.0.nori
    bento/
        my-package/                     # installed package
            nori.toml                   # manifest copy
            lib/
                mylib.slib
            bin/
                mytool
            data/
                config.toml
```

| Directory | Purpose |
|-----------|---------|
| `bin/`    | Symlinks to package executables, add to `PATH` for access |
| `cache/`  | Cached `.nori` archives from installations |
| `bento/`  | Installed package contents, one directory per package |

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
| `nori install <archive>` | Install from a `.nori` file |
| `nori install <name> from <path>` | Install from a directory or archive source |
| `nori list` | List all installed packages |
| `nori info <name>` | Show details about an installed package |
| `nori remove <name>` | Uninstall a package |

## Limitations

1. **No build step**: Nori does not compile Sushi source. Use `sushic` to compile first.
2. **Local sources only**: Remote installation (HTTP, registry) is not yet available.
3. **No dependency resolution**: The `[dependencies]` section is reserved for future use.
4. **Platform-specific**: `.slib` files are not portable across platforms (same as the compiler).
5. **No version constraints**: Installing a package always replaces any existing version.

## See Also

- [Libraries](libraries.md) - Creating and linking Sushi libraries
- [Library Format](library-format.md) - `.slib` binary format specification
- [Compiler Reference](compiler-reference.md) - All compiler CLI options
- [Getting Started](getting-started.md) - Introduction to Sushi
