#!/bin/bash
set -e

echo "Checking out sushi-public..."
git checkout sushi-public

echo "Resetting tree to main..."
git read-tree -u --reset main

# Fix badge gist ID in README (dev -> public)
echo "Fixing badge URLs in README.md..."
sed -i "" "s|e893b92ba7b7561fae565f832f83159d|5bfbd5156dec23acfe18dd7956e251ba|g" README.md

# Remove all root .md files except README.md, STYLE.md, CHANGELOG.md
echo "Removing unwanted .md files..."
find . -maxdepth 1 -name "*.md" ! -name "README.md" ! -name "STYLE.md" ! -name "CHANGELOG.md" -delete

# Remove .claude directory and other files
echo "Removing .claude directory and scripts..."
rm -rf .claude
rm -f test-docker-build.sh
rm -f memory_monitor.sh

# Self-destruct
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
rm -f "$SCRIPT_PATH"

echo "Staging changes..."
git add -A

echo "Committing..."
git commit -m "Sync with main"

echo "Ready to push: git push --dry-run public sushi-public:main"
