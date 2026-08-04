#!/usr/bin/env python3
"""Create the SQLite database with the required schema.

This script is idempotent: re-running it will create tables if they do not exist.
"""
from pathlib import Path
import sqlite3
from typing import List

DB_PATH = Path("database/curriculum.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SQL_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS programmes (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE,
    title TEXT,
    exam_board TEXT,
    phase TEXT
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    programme_id INTEGER REFERENCES programmes(id) ON DELETE CASCADE,
    slug TEXT,
    title TEXT,
    subject TEXT,
    year_group TEXT,
    sequence_order INTEGER,
    description TEXT,
    UNIQUE(programme_id, slug)
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER REFERENCES units(id) ON DELETE CASCADE,
    slug TEXT,
    title TEXT,
    lesson_number INTEGER,
    url TEXT,
    UNIQUE(unit_id, slug)
);

CREATE TABLE IF NOT EXISTS lesson_resources (
    id INTEGER PRIMARY KEY,
    lesson_id INTEGER REFERENCES lessons(id) ON DELETE CASCADE,
    resource_type TEXT,
    title TEXT,
    url TEXT
);

CREATE TABLE IF NOT EXISTS learning_objectives (
    id INTEGER PRIMARY KEY,
    lesson_id INTEGER REFERENCES lessons(id) ON DELETE CASCADE,
    objective TEXT
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY,
    lesson_id INTEGER REFERENCES lessons(id) ON DELETE CASCADE,
    keyword TEXT
);

CREATE TABLE IF NOT EXISTS downloads (
    lesson_id INTEGER PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
    last_checked TEXT,
    etag TEXT,
    sha256 TEXT
);

CREATE INDEX IF NOT EXISTS idx_units_programme ON units(programme_id);
CREATE INDEX IF NOT EXISTS idx_lessons_unit ON lessons(unit_id);
"""


def create_schema(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.executescript(SQL_SCHEMA)
        conn.commit()
        print(f"Created schema at {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    create_schema()
