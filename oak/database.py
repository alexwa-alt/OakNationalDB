"""Database access helpers.

This module intentionally keeps SQL close to the metal to avoid heavy ORM
dependencies. It exposes simple upsert functions used by the importer.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

from oak import models
from config import config


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db = db_path or config.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def upsert_programme(p: models.Programme, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO programmes (slug, title, exam_board, phase) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(slug) DO UPDATE SET title=excluded.title, exam_board=excluded.exam_board, phase=excluded.phase",
        (p.slug, p.title, p.exam_board, p.phase),
    )
    conn.commit()
    cur.execute("SELECT id FROM programmes WHERE slug = ?", (p.slug,))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


def upsert_unit(u: models.Unit, programme_id: int, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO units (programme_id, slug, title, subject, year_group, sequence_order, description)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(programme_id, slug) DO UPDATE SET title=excluded.title, subject=excluded.subject, year_group=excluded.year_group, sequence_order=excluded.sequence_order, description=excluded.description",
        (programme_id, u.slug, u.title, u.subject, u.year_group, u.sequence_order, u.description),
    )
    conn.commit()
    cur.execute("SELECT id FROM units WHERE programme_id = ? AND slug = ?", (programme_id, u.slug))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


def upsert_lesson(l: models.Lesson, unit_id: int, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lessons (unit_id, slug, title, lesson_number, url) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(unit_id, slug) DO UPDATE SET title=excluded.title, lesson_number=excluded.lesson_number, url=excluded.url",
        (unit_id, l.slug, l.title, l.lesson_number, l.url),
    )
    conn.commit()
    cur.execute("SELECT id FROM lessons WHERE unit_id = ? AND slug = ?", (unit_id, l.slug))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


# Additional upserts for resources, objectives, keywords and downloads would be added here.
