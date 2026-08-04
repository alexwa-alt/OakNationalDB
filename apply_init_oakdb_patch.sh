#!/usr/bin/env bash
set -euo pipefail

# apply_init_oakdb_patch.sh
# Usage: ./apply_init_oakdb_patch.sh
# Creates/switches to branch init-oakdb, writes files, commits and pushes.

# Ensure we're in a git repository
if [ ! -d .git ]; then
  echo "Error: this does not look like a git repository (no .git directory)." >&2
  exit 1
fi

BRANCH="init-oakdb"

# Create and switch to branch (if exists, switch to it)
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "Switching to existing branch ${BRANCH}"
  git checkout "${BRANCH}"
else
  echo "Creating and switching to branch ${BRANCH}"
  git checkout -b "${BRANCH}"
fi

# Ensure directories exist
mkdir -p commands
mkdir -p .github/workflows
mkdir -p tests

# Write commands/download.py
cat > commands/download.py <<'PY'
"""Download command: run the importer to populate DB and cache.

This script is safe to run before an API key is configured: the importer will
use cached files if available and will not fail outright when network is
unavailable.
"""
from oak.importer import Importer

def main():
    imp = Importer()
    imp.import_all()

if __name__ == "__main__":
    main()
PY

# Write GitHub Actions workflow
cat > .github/workflows/python.yml <<'YML'
# Run tests on push
name: Python package

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: [3.12]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest
      - name: Run tests
        run: |
          pytest -q
YML

# Write tests/test_importer.py
cat > tests/test_importer.py <<'PYTEST'
"""Tests for importer adapters and DB round-trip using a fake API client."""
from pathlib import Path
import sqlite3

import pytest

from config import config
import setup_database
from oak.importer import Importer


class FakeClient:
    def __init__(self):
        pass

    def get_with_cache(self, path, cache_key):
        # Return sample responses depending on path
        if path.startswith("/programmes"):
            # programmes list containing our target
            return {"results": [{"slug": "science-secondary-aqa", "title": "Science (AQA)"}]}
        if path.startswith("/programmes/science-secondary-aqa/units") or path.startswith("/programmes/science-secondary-aqa/units"):
            # units list
            return {"results": [
                {"slug": "unit-1", "title": "Forces and Motion", "subject": "Physics", "year_group": "Year 9", "sequence_order": 1},
                {"slug": "unit-2", "title": "Atomic Structure", "subject": "Chemistry", "year_group": "Year 10", "sequence_order": 2},
            ]}
        if path.startswith("/units/unit-1"):
            return {"lessons": [
                {"slug": "l1", "title": "Introduction to Forces", "url": "https://www.thenational.academy/lessons/l1", "lesson_number": 1},
                {"slug": "l2", "title": "Balanced and Unbalanced Forces", "url": "https://www.thenational.academy/lessons/l2", "lesson_number": 2},
            ]}
        if path.startswith("/units/unit-2"):
            return {"lessons": [
                {"slug": "l3", "title": "Atoms and Elements", "url": "https://www.thenational.academy/lessons/l3", "lesson_number": 1},
            ]}
        raise RuntimeError(f"Unexpected path in fake client: {path}")

def test_importer_creates_programme_units_lessons(tmp_path):
    # Use a temporary database path for isolation
    db_path = tmp_path / "test.db"
    config.db_path = db_path
    # Create schema at test DB
    setup_database.create_schema(db_path)

    client = FakeClient()
    imp = Importer(client=client)
    imp.import_programme("science-secondary-aqa")

    # Verify DB contents
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT slug, title FROM programmes")
    programmes = cur.fetchall()
    assert len(programmes) == 1
    assert programmes[0][0] == "science-secondary-aqa"

    cur.execute("SELECT slug, title FROM units ORDER BY sequence_order")
    units = cur.fetchall()
    assert len(units) == 2
    assert units[0][0] == "unit-1"
    assert units[1][0] == "unit-2"

    cur.execute("SELECT slug, title, url FROM lessons ORDER BY lesson_number")
    lessons = cur.fetchall()
    assert len(lessons) == 3
    # check some lesson urls
    urls = [r[2] for r in lessons]
    assert "https://www.thenational.academy/lessons/l1" in urls
    assert "https://www.thenational.academy/lessons/l3" in urls

    conn.close()
PYTEST

# Write README_API.md
cat > README_API.md <<'MD'
## Using the API key

Once you have your Oak API key, set it as an environment variable and run the importer:

```bash
export OAK_API_KEY="your_api_key_here"
python commands/update.py        # or python commands/download.py
