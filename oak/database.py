"""Database access helpers.

This module intentionally keeps SQL close to the metal to avoid heavy ORM
dependencies. It exposes simple upsert functions used by the importer.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional, Iterable, List, Tuple

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


def upsert_lesson_resource(lesson_id: int, r_type: str, title: str, url: str, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lesson_resources (lesson_id, resource_type, title, url) VALUES (?, ?, ?, ?)",
        (lesson_id, r_type, title, url),
    )
    conn.commit()
    cur.execute("SELECT id FROM lesson_resources WHERE lesson_id = ? AND url = ?", (lesson_id, url))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


def upsert_learning_objective(lesson_id: int, objective: str, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO learning_objectives (lesson_id, objective) VALUES (?, ?)",
        (lesson_id, objective),
    )
    conn.commit()
    cur.execute("SELECT id FROM learning_objectives WHERE lesson_id = ? AND objective = ?", (lesson_id, objective))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


def upsert_keyword(lesson_id: int, keyword: str, conn: Optional[sqlite3.Connection] = None) -> int:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO keywords (lesson_id, keyword) VALUES (?, ?)",
        (lesson_id, keyword),
    )
    conn.commit()
    cur.execute("SELECT id FROM keywords WHERE lesson_id = ? AND keyword = ?", (lesson_id, keyword))
    row = cur.fetchone()
    if own:
        conn.close()
    return row[0]


def upsert_download_record(lesson_id: int, last_checked: Optional[str], etag: Optional[str], sha256: Optional[str], conn: Optional[sqlite3.Connection] = None) -> None:
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO downloads (lesson_id, last_checked, etag, sha256) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(lesson_id) DO UPDATE SET last_checked=excluded.last_checked, etag=excluded.etag, sha256=excluded.sha256",
        (lesson_id, last_checked, etag, sha256),
    )
    conn.commit()
    if own:
        conn.close()


def get_all_lesson_urls(conn: Optional[sqlite3.Connection] = None) -> List[Tuple[int, str]]:
    """Return list of (lesson_id, url) for the scraper to consume."""
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    cur.execute("SELECT id, url FROM lessons WHERE url IS NOT NULL")
    rows = cur.fetchall()
    if own:
        conn.close()
    return [(r[0], r[1]) for r in rows]


# Bulk helpers

def bulk_upsert_lessons(lessons: Iterable[models.Lesson], unit_map: dict, conn: Optional[sqlite3.Connection] = None) -> None:
    """Bulk upsert lessons where unit_map maps unit_slug -> unit_id.

    This helper commits at the end of the batch and is intended for faster imports.
    """
    own = False
    if conn is None:
        conn = _get_conn()
        own = True
    cur = conn.cursor()
    for l in lessons:
        unit_id = unit_map.get(l.unit_slug)
        if unit_id is None:
            raise ValueError(f"Unknown unit slug: {l.unit_slug}")
        cur.execute(
            "INSERT INTO lessons (unit_id, slug, title, lesson_number, url) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(unit_id, slug) DO UPDATE SET title=excluded.title, lesson_number=excluded.lesson_number, url=excluded.url",
            (unit_id, l.slug, l.title, l.lesson_number, l.url),
        )
    conn.commit()
    if own:
        conn.close()
