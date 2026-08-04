"""Basic tests for models module."""
from oak import models


def test_lesson_dataclass():
    l = models.Lesson(slug="lsn-1", title="Intro", url="http://example/1", lesson_number=1, unit_slug="unit-1")
    assert l.slug == "lsn-1"
