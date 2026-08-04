"""Dataclasses used throughout the importer and database layer.

Keep this module independent of any external libraries so it can be used in tests.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Programme:
    slug: str
    title: str
    exam_board: Optional[str] = None
    phase: Optional[str] = None


@dataclass
class Unit:
    slug: str
    title: str
    programme_slug: str
    subject: Optional[str] = None
    year_group: Optional[str] = None
    sequence_order: Optional[int] = None
    description: Optional[str] = None


@dataclass
class Lesson:
    slug: str
    title: str
    url: str
    lesson_number: Optional[int]
    unit_slug: str


@dataclass
class LessonResource:
    lesson_slug: str
    resource_type: str
    title: str
    url: str


@dataclass
class LearningObjective:
    lesson_slug: str
    objective: str


@dataclass
class Keyword:
    lesson_slug: str
    keyword: str


@dataclass
class DownloadRecord:
    lesson_slug: str
    last_checked: Optional[str]
    etag: Optional[str]
    sha256: Optional[str]
