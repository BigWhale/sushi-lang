# Nori - Sushi Lang Package Manager

Nori is the package manager for Sushi Lang, distributed alongside the compiler in the same wheel.

## Quick Start

```bash
# Create a new package manifest
nori init

# Build a .nori archive
nori build

# Install from archive
nori install ./dist/my-package-1.0.0.nori

# Install from directory containing .nori files
nori install my-package from ./dist/

# List installed packages
nori list

# Show package details
nori info my-package

# Remove a package
nori remove my-package
```

## Manifest Format (`nori.toml`)

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
# Future: version-constrained deps
```

## Workflow

1. Compile your library: `sushic --lib mylib.sushi`
2. Create `nori.toml` referencing the outputs
3. Build: `nori build` (creates `dist/my-package-1.0.0.nori`)
4. Install: `nori install ./dist/my-package-1.0.0.nori`
5. The compiler automatically finds installed libraries (no `SUSHI_LIB_PATH` needed)

## Directory Structure

Packages install to `~/.sushi/`:

```
~/.sushi/
    bin/                  # executable symlinks
    cache/                # downloaded .nori archives
    bento/
        my-package/       # installed package
            nori.toml
            lib/          # .slib files
            bin/          # executables
            data/         # data files
```

## Compiler Integration

The compiler searches for libraries in this order:
1. `SUSHI_LIB_PATH` directories (if set)
2. Nori bento packages (`~/.sushi/bento/*/lib/`)
3. Current directory

## Archive Format

`.nori` files are gzip-compressed tarballs containing:
```
{name}-{version}/
    nori.toml
    lib/
    bin/
    data/
```
