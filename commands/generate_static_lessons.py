#!/usr/bin/env python3
"""
Generator that:
- fetches units from the Open API sequence (API sequence slug),
- fetches the corresponding teacher unit pages (teacher sequence slug),
- scrapes lesson links from those pages,
- validates/fetches lesson metadata from Open API /lessons/{lessonSlug}/summary,
- writes site/index.html with the discovered lesson links.

Behaviour: group links by teacher unit title (one section per Oak unit).
Lessons are deduplicated globally (first seen wins) so a lesson appearing
in multiple teacher units will only be shown under the first unit it was
encountered during the scan.

Fixes included:
- Use API-fetched lesson title where available so the link text is the
  canonical lesson title (avoids including the lesson objective in the
  anchor text).
- Use natural sort for lesson ordering so "10" sorts after "9" not before "1".
- If API title is unavailable, clean the scraped title removing 'I can' objectives
  and fixing missing punctuation for numeric prefixes (e.g., '1What' -> '1. What').
"""

from __future__ import annotations
import os
import sys
import time
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
API_SEQ = os.environ.get("OAK_API_SEQUENCE", "science-secondary-aqa")
TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-ks3")

API_BASE = "https://open-api.thenational.academy/api/v0"
TEACHER_BASE = "https://www.thenational.academy/teachers/programmes"

HEADERS = {"Accept": "application/json", "User-Agent": "oak-scrapegen/1.0"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"
REQUEST_TIMEOUT = 15


def get_json(url: str) -> Tuple[int, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.json() if r.content else None
    except Exception as e:
        print(f"JSON GET error for {url}: {e}", file=sys.stderr)
        return 0, None


def get_html(url: str) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers={"User-Agent": "oak-scrapegen/1.0"}, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        print(f"HTML GET error for {url}: {e}", file=sys.stderr)
        return 0, ""


def fetch_units(api_sequence_slug: str) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sequences/{api_sequence_slug}/units"
    print(f"Fetching units from API: {url}")
    status, j = get_json(url)
    if status != 200 or not isinstance(j, list):
        print(f"Failed to fetch units: status={status} json_type={type(j)}", file=sys.stderr)
        return []
    return j


def get_html_from_teacher_unit_url(url: str) -> str:
    status, html = get_html(url)
    if status != 200:
        print(f"HTMl fetch returned {status} for {url}", file=sys.stderr)
        return ""
    return html


def fetch_units_from_teacher(api_sequence_slug: str, teacher_sequence_slug: str) -> List[Tuple[str, str]]:
    """Return a list of (title, url) for teacher unit pages discovered for the sequence."""
    units_url = f"{TEACHER_BASE}/{teacher_sequence_slug}/units"
    print(f"Fetching teacher units listing: {units_url}")
    status, html = get_html(units_url)
    if status != 200:
        print(f"Failed to get teacher units page: {status}", file=sys.stderr)
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/units/" in href:
            title = (a.get_text() or href).strip()
            full = urljoin(TEACHER_BASE, href)
            links.append((title, full))
    return links


def fetch_units_from_api(api_sequence_slug: str) -> List[Tuple[str, str]]:
    # Fallback: build unit links from API unit slugs
    units = fetch_units(api_sequence_slug)
    out: List[Tuple[str, str]] = []
    for u in units:
        slug = u.get("slug")
        title = u.get("title") or slug
        if slug:
            out.append((title, f"{TEACHER_BASE}/{TEACHER_SEQ}/units/{slug}"))
    return out


def get_teacher_unit_lessons(teacher_unit_url: str) -> List[Tuple[str, str]]:
    """Scrape a teacher unit page for lesson links. Returns list of (raw_title, href)"""
    html = get_html_from_teacher_unit_url(teacher_unit_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # lessons path pattern in teacher pages
        if "/lessons/" in href:
            raw_title = (a.get_text() or href).strip()
            full = urljoin(TEACHER_BASE, href) if not href.startswith("http") else href
            links.append((raw_title, full))
    # de-duplicate by href while preserving the last title seen for that page on the unit
    seen = {}
    for t, u in links:
        seen[u] = t
    result = [(seen[u], u) for u in seen]
    return result


def fetch_lesson_summary(lesson_slug: str) -> Tuple[int, Any]:
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    return get_json(url)


def extract_lesson_slug_from_url(url: str) -> str:
    """Extract the lesson slug from a teacher or public lesson URL."""
    try:
        p = urlparse(url)
        path = p.path  # e.g. /lessons/what-forces-do
        if "/lessons/" in path:
            slug = path.split("/lessons/", 1)[1].strip("/ ")
            # slug might include further segments; take first
            slug = slug.split("/")[0]
            return slug
    except Exception:
        pass
    return ""


def clean_title(raw: str) -> str:
    """Clean raw link text as a fallback when API title isn't available.
    - remove objectives that begin with phrases like 'I can'
    - ensure a space after a leading digit (e.g. '1What' -> '1. What')
    - collapse whitespace
    """
    if not raw:
        return ""
    s = raw.replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    # remove common objective marker 'I can' and anything after it
    m = re.search(r"(.+?)(?:\bI can\b).*", s, flags=re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    # put a dot+space after leading number if missing
    s = re.sub(r"^(\d+)([A-Za-z])", r"\1. \2", s)
    # if number is followed immediately by word without punctuation, add '. '
    s = re.sub(r"^(\d+)(\s*)([A-Za-z])", lambda mo: f"{int(mo.group(1))}. {mo.group(3)}" if mo.group(2)=="" else mo.group(0), s)
    return s.strip()


def lesson_sort_key(title: str) -> Tuple[int, str]:
    """Natural sort key: leading number (if present) then remainder lowercased."""
    if not title:
        return (9999, "")
    m = re.match(r"^\s*(\d+)[\.)\s-]*\s*(.*)$", title)
    if m:
        num = int(m.group(1))
        rest = m.group(2) or ""
        return (num, rest.lower())
    # try to find leading digits stuck to words e.g. '1What'
    m2 = re.match(r"^\s*(\d+)([A-Za-z].*)$", title)
    if m2:
        num = int(m2.group(1))
        rest = m2.group(2)
        return (num, rest.lower())
    return (9999, title.lower())


def main():
    print("Starting generator. API key present:", bool(API_KEY))
    # discover teacher unit pages using teacher sequence listing; fallback to api sequence
    teacher_units = fetch_units_from_teacher(API_SEQ, TEACHER_SEQ)
    if not teacher_units:
        teacher_units = fetch_units_from_api(API_SEQ)

    print(f"Found {len(teacher_units)} teacher units to scan")

    # Map of unit title -> list of (title, href)
    from collections import OrderedDict
    unit_map: "OrderedDict[str, List[Tuple[str, str]]]" = OrderedDict()
    global_seen = set()  # hrefs seen so far; used for global dedupe (first seen wins)

    for title, unit_url in teacher_units:
        print(f"Scanning unit: {title} -> {unit_url}")
        try:
            lessons = get_teacher_unit_lessons(unit_url)
            print(f"  found {len(lessons)} lesson links")
            unit_list: List[Tuple[str, str]] = []
            for raw_title, href in lessons:
                if href in global_seen:
                    # skip duplicate lessons already added under an earlier unit
                    continue
                # try to fetch canonical title from API using slug
                slug = extract_lesson_slug_from_url(href)
                canonical_title = None
                if slug:
                    status, data = fetch_lesson_summary(slug)
                    if status == 200 and isinstance(data, dict):
                        # try common keys
                        for key in ("title", "lessonTitle", "name", "displayName"):
                            if key in data and isinstance(data[key], str) and data[key].strip():
                                canonical_title = data[key].strip()
                                break
                        # sometimes nested
                        if not canonical_title and "data" in data and isinstance(data["data"], dict):
                            for key in ("title", "name"): 
                                if key in data["data"] and isinstance(data["data"][key], str):
                                    canonical_title = data["data"][key].strip()
                                    break
                    # small throttle for API calls
                    time.sleep(0.1)
                if not canonical_title:
                    canonical_title = clean_title(raw_title)
                global_seen.add(href)
                unit_list.append((canonical_title, href))
            # sort unit's lessons by natural numeric order
            unit_list.sort(key=lambda x: lesson_sort_key(x[0] or ""))
            unit_map[title] = unit_list
            # small throttle between unit pages
            time.sleep(0.25)
        except Exception as e:
            print(f"Error scanning {unit_url}: {e}", file=sys.stderr)

    total_links = sum(len(v) for v in unit_map.values())

    # build html
    html_lines: List[str] = []
    html_lines.append("<!doctype html>")
    html_lines.append("<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Oak lesson links</title>")
    html_lines.append("<style>body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial;padding:1rem}a{color:#0366d6}h2{margin-top:1.5rem}</style>")
    html_lines.append("</head><body>")
    html_lines.append(f"<h1>Oak lesson links — sequence: {API_SEQ}</h1>")
    html_lines.append(f"<p>Total links discovered: {total_links}</p>")

    if total_links == 0:
        html_lines.append("<p>No links discovered. See Actions logs for details.</p>")
    else:
        # render each unit as its own section
        for idx, (unit_title, lessons) in enumerate(unit_map.items(), start=1):
            # skip empty units
            if not lessons:
                continue
            safe_unit = (unit_title or f"Unit {idx}").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(f"<h2>{safe_unit}</h2>")
            html_lines.append("<ul>")
            for title, href in lessons:
                safe = (title or href).replace("<", "&lt;").replace(">", "&gt;")
                html_lines.append(f'<li><a href="{href}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
            html_lines.append("</ul>")

    html_lines.append("</body></html>")

    OUT_PATH.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {total_links} links across {len([u for u in unit_map.values() if u])} unit(s)")


if __name__ == "__main__":
    main()
