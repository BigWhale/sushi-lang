#!/bin/bash
# setup_libs.sh - Compile helper libraries for library integration tests
#
# This script must be run before running the library tests.
# Usage: ./tests/libs/setup_libs.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPERS_DIR="$SCRIPT_DIR/helpers"
BIN_DIR="$SCRIPT_DIR/bin"

# Create bin directory if it doesn't exist
mkdir -p "$BIN_DIR"

echo "Compiling library test helpers..."

# Compile each helper library
for lib in "$HELPERS_DIR"/*.sushi; do
    name=$(basename "$lib" .sushi)
    echo "  Compiling $name..."
    ./sushic --lib "$lib" -o "$BIN_DIR/$name.bc"
done

echo "Done. Libraries compiled to $BIN_DIR"
echo "Set SUSHI_LIB_PATH=$BIN_DIR to use these libraries"
