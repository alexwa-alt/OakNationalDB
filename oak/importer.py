"""Importer: fetches raw API JSON via ApiClient, caches responses and converts
JSON into dataclasses which are then written into the SQLite database.

All API-field assumptions live in the `_adapters` section. When the real API
responses arrive, update the adapters rather than rewriting the importer flow.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from oak.api import ApiClient
from oak import cache
from oak import models
from oak import database
from config import config

logger = logging.getLogger(__name__)


class Importer:
    def __init__(self, client: Optional[ApiClient] = None):
        self.client = client or ApiClient()

    # --- Adapters / mapping layer ---
    # Keep all assumptions about the API response structure here. When the API
    # key is available we will replace or extend these adapters with precise
    # mappings for real responses. The goal is to centralise changes.

    def _adapt_programmes_list(self, raw: Any) -> List[models.Programme]:
        """Convert programmes list JSON into Programme dataclasses.

        Assumptions (document here so they are easy to change):
        - raw is a dict with key 'results' or is already a list of programmes.
        - each programme has 'slug' and 'title'; optional 'exam_board' and 'phase'.
        """
        items = []
        candidates = None
        if isinstance(raw, dict):
            candidates = raw.get("results") or raw.get("programmes") or raw.get("data")
        if candidates is None:
            if isinstance(raw, list):
                candidates = raw
        candidates = candidates or []
        for p in candidates:
            slug = p.get("slug") or p.get("id") or p.get("key")
            title = p.get("title") or p.get("name")
            exam_board = p.get("exam_board") or p.get("examBoard")
            phase = p.get("phase")
            if not slug or not title:
                logger.debug("Skipping programme missing slug/title: %s", p)
                continue
            items.append(models.Programme(slug=slug, title=title, exam_board=exam_board, phase=phase))
        return items

    def _adapt_units_list(self, raw: Any, programme_slug: str) -> List[models.Unit]:
        """Convert a units list JSON into Unit dataclasses.

        Assumptions:
        - raw contains a list under 'results' / 'units' / 'data'.
        - each unit has 'slug' and 'title'. Optional fields mapped where available.
        """
        items = []
        candidates = None
        if isinstance(raw, dict):
            candidates = raw.get("results") or raw.get("units") or raw.get("data")
        if candidates is None and isinstance(raw, list):
            candidates = raw
        candidates = candidates or []
        for u in candidates:
            slug = u.get("slug") or u.get("id")
            title = u.get("title") or u.get("name")
            subject = u.get("subject") or u.get("topic")
            year_group = u.get("year_group") or u.get("yearGroup") or u.get("year")
            sequence_order = u.get("sequence_order") or u.get("order")
            description = u.get("description") or u.get("summary")
            if not slug or not title:
                logger.debug("Skipping unit missing slug/title: %s", u)
                continue
            items.append(models.Unit(slug=slug, title=title, programme_slug=programme_slug,
                                     subject=subject, year_group=year_group,
                                     sequence_order=sequence_order, description=description))
        return items

    def _adapt_lessons_list(self, raw: Any, unit_slug: str) -> List[models.Lesson]:
        """Convert lessons list JSON into Lesson dataclasses.

        Assumptions:
        - raw contains 'lessons' / 'items' / 'data' list.
        - lesson objects include 'slug', 'title', 'url' or 'path', and optionally 'lesson_number'.
        """
        items = []
        candidates = None
        if isinstance(raw, dict):
            candidates = raw.get("results") or raw.get("lessons") or raw.get("items") or raw.get("data")
        if candidates is None and isinstance(raw, list):
            candidates = raw
        candidates = candidates or []
        for l in candidates:
            slug = l.get("slug") or l.get("id")
            title = l.get("title") or l.get("name")
            url = l.get("url") or l.get("link") or l.get("path")
            lesson_number = l.get("lesson_number") or l.get("number") or l.get("order")
            if url and url.startswith("/"):
                url = config.base_url.rstrip("/") + url
            if not slug or not title or not url:
                logger.debug("Skipping lesson missing slug/title/url: %s", l)
                continue
            items.append(models.Lesson(slug=slug, title=title, url=url, lesson_number=lesson_number, unit_slug=unit_slug))
        return items

    # --- Fetch + cache helpers ---

    def _fetch_and_cache(self, path: str, cache_key: str) -> Any:
        """Fetch a path from the API and cache the raw JSON under cache_key.

        Uses conditional requests with ETag when possible and falls back to raw GET.
        """
        # Try to load existing cache meta to perform conditional requests
        cached = cache.load_cache(cache_key)
        etag = None
        if cached:
            _, meta = cached
            etag = meta.get("etag")

        # Prefer ApiClient conditional GET if available
        try:
            if hasattr(self.client, "get_with_cache"):
                resp = self.client.get_with_cache(path, cache_key)
            else:
                resp = self.client.get(path)
            # Save the raw response to cache; the ApiClient or cache layer will
            # record ETag/meta where possible.
            cache.save_cache(cache_key, resp)
            return resp
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", path, e)
            # On failure, return cached data if present
            if cached:
                data, _ = cached
                logger.info("Using cached data for %s", cache_key)
                return data
            raise

    # --- High-level import orchestration ---

    def import_programme(self, programme_slug: str) -> None:
        """Import a programme and all of its units and lessons.

        This function is written to be resumable: each upsert is committed and
        the cached raw JSON is persisted so re-running will pick up progress.
        """
        # 1. Fetch programmes list (this may be broader than we need)
        programmes_key = "programmes_aqa_secondary"
        programmes_json = self._fetch_and_cache("/programmes?exam_board=aqa&phase=secondary", programmes_key)
        programmes = self._adapt_programmes_list(programmes_json)
        target = next((p for p in programmes if p.slug == programme_slug), None)
        if not target:
            logger.warning("Programme %s not found in programmes list; will still insert a placeholder.", programme_slug)
            target = models.Programme(slug=programme_slug, title=programme_slug)
        programme_id = database.upsert_programme(target)
        logger.info("Upserted programme %s -> id %s", target.slug, programme_id)

        # 2. Fetch units for the programme
        units_key = f"units_{programme_slug}"
        # The exact path will need to be adapted when real API is known. This is a
        # reasonable placeholder which will be updated in one place.
        units_json = self._fetch_and_cache(f"/programmes/{programme_slug}/units", units_key)
        units = self._adapt_units_list(units_json, programme_slug)

        for u in units:
            unit_id = database.upsert_unit(u, programme_id)
            logger.info("Upserted unit %s -> id %s", u.slug, unit_id)
            # Fetch unit details including lessons
            unit_key = f"unit_{u.slug}"
            unit_json = self._fetch_and_cache(f"/units/{u.slug}", unit_key)
            lessons = self._adapt_lessons_list(unit_json, u.slug)
            for l in lessons:
                lesson_id = database.upsert_lesson(l, unit_id)
                logger.info("Upserted lesson %s -> id %s", l.slug, lesson_id)

    def import_all(self) -> None:
        """Convenience to import the canonical AQA secondary science programme.

        This is the entry point that commands/update.py will call once the API
        key is present. It can be extended to handle multiple programmes.
        """
        self.import_programme("science-secondary-aqa")


# Simple CLI utility used by commands/update.py
def run_import():
    imp = Importer()
    imp.import_all()


if __name__ == "__main__":
    run_import()
