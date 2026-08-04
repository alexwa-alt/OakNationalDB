#!/usr/bin/env python3
"""
Scrape teacher programme units page, group lessons by unit, canonicalize via API.

Usage:
- Ensure your workflow provides OAK_API_KEY (optional but recommended).
- Optionally set OAK_API_SEQUENCE (default: science-secondary-aqa)
- Optionally set OAK_TEACHER_SEQUENCE (default: science-secondary-aqa)

This script:
- builds unitSlug -> unitTitle map from API /sequences/{API_SEQ}/units
- fetches https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}/units
- scrapes teacher lesson links (requests + BeautifulSoup)
- FALLBACK: renders page with Playwright if requests fails to find lesson links
- for each lesson slug found, tries GET /lessons/{lessonSlug}/summary and uses canonical /lessons/{lessonSlug} URL and title when available
- writes site/index.html grouped by unit with a top navigation
"""
from __future__ import annotations
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set

API_KEY = os.environ.get("OAK_API_KEY")
API_SEQ = os.environ.get("OAK_API_SEQUENCE", "science-secondary-aqa")
TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-aqa")

API_BASE = "https://open-api.thenational.academy/api/v0"
TEACHER_UNITS_URL = f"https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}/units"

HEADERS = {"Accept": "application/json", "User-Agent": "oak-static-generator/1.0"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site"); OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"
REQUEST_TIMEOUT = 15

# Playwright lazy import
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

UNIT_LESSON_RE = re.compile(r"/teachers/programmes/[^/]+/units/([^/]+)/lessons/([^/?#]+)")

def get_json(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.status_code, r.json()
        return r.status_code, None
    except Exception as e:
        print(f"JSON request failed for {url}: {e}")
        return 0, None

def get_html(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": "oak-scraper/1.0"}, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        print(f"HTML request failed for {url}: {e}")
        return 0, ""

def fetch_units_map(sequence_slug: str) -> Dict[str,str]:
    url = f"{API_BASE}/sequences/{sequence_slug}/units"
    status, j = get_json(url)
    mapping: Dict[str,str] = {}
    if status == 200 and isinstance(j, list):
        for entry in j:
            if isinstance(entry, dict) and entry.get("units"):
                for u in entry["units"]:
                    if isinstance(u, dict):
                        slug = u.get("unitSlug") or u.get("slug") or u.get("id")
                        title = u.get("unitTitle") or u.get("title") or slug
                        if slug:
                            mapping[slug] = title
            else:
                if isinstance(entry, dict) and ("unitSlug" in entry or "slug" in entry or "id" in entry):
                    slug = entry.get("unitSlug") or entry.get("slug") or entry.get("id")
                    title = entry.get("unitTitle") or entry.get("title") or slug
                    if slug:
                        mapping[slug] = title
    else:
        print(f"Warning: failed to build unit map from API (status {status}). Unit titles may be missing.")
    return mapping

def parse_lesson_links_from_html(html: str) -> List[Tuple[str,str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: Set[Tuple[str,str]] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urljoin("https://www.thenational.academy", href)
        if "/teachers/programmes/" in href and "/lessons/" in href:
            title = a.get_text(strip=True) or href
            found.add((title, href))
    return sorted(list(found), key=lambda x: x[1])

def playwright_render_and_parse(url: str) -> List[Tuple[str,str]]:
    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not available; skipping render fallback.")
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, timeout=30000)
            content = page.content()
            browser.close()
    except Exception as e:
        print(f"Playwright error navigating to {url}: {e}")
        return []
    return parse_lesson_links_from_html(content)

def fetch_canonical_summary(lesson_slug: str):
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    status, j = get_json(url)
    return status, j if isinstance(j, dict) else {}

def slug_from_teacher_url(url: str) -> Tuple[str,str]:
    m = UNIT_LESSON_RE.search(urlparse(url).path)
    if m:
        return m.group(1), m.group(2)
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 1:
        if "lessons" in parts:
            idx = parts.index("lessons")
            lesson_slug = parts[idx+1] if idx+1 < len(parts) else ""
            unit_slug = parts[idx-2] if idx-2 >=0 else ""
            return unit_slug, lesson_slug
        return "", parts[-1]
    return "", url

def build_index():
    print(f"Building unit map from API sequence: {API_SEQ}")
    unit_map = fetch_units_map(API_SEQ)

    print(f"Fetching teacher programme units page: {TEACHER_UNITS_URL}")
    status, html = get_html(TEACHER_UNITS_URL)
    links: List[Tuple[str,str]] = []
    if status == 200 and html:
        links = parse_lesson_links_from_html(html)
        print(f"Found {len(links)} lesson links via requests on units page.")
    if not links:
        print("No links from requests; trying Playwright render of the units page.")
        links = playwright_render_and_parse(TEACHER_UNITS_URL)
        print(f"Found {len(links)} lesson links via Playwright render.")

    grouped: Dict[str, Dict[str,Any]] = {}
    for title, href in links:
        unit_slug, lesson_slug = slug_from_teacher_url(href)
        if not lesson_slug:
            continue
        status, summary = fetch_canonical_summary(lesson_slug)
        if status == 200 and summary:
            canonical_title = summary.get("lessonTitle") or summary.get("title") or title
            canonical_url = f"https://www.thenational.academy/lessons/{lesson_slug}"
            final_title = canonical_title
            final_url = canonical_url
        else:
            final_title = title
            final_url = href
        unit_title = unit_map.get(unit_slug, unit_slug or "Unmapped unit")
        if unit_slug not in grouped:
            grouped[unit_slug] = {"title": unit_title, "lessons": []}
        if all(final_url != existing[1] for existing in grouped[unit_slug]["lessons"]):
            grouped[unit_slug]["lessons"].append((final_title, final_url, lesson_slug))
        time.sleep(0.03)

    # sort units nicely by title
    sorted_units = sorted(grouped.items(), key=lambda it: (it[1]["title"] or it[0]).lower())
    total = sum(len(v["lessons"]) for _, v in sorted_units)
    print(f"Collected {total} lessons across {len(sorted_units)} units.")

    # build a top navigation
    lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} nav{background:#f6f8fa;padding:.5rem;border-radius:6px} h2{margin-top:1.25rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — programme: {TEACHER_SEQ}</h1>",
        "<p>Automatically generated. Lessons grouped by unit. Canonical lesson pages used when available.</p>",
    ]
    if sorted_units:
        lines.append("<nav><strong>Units:</strong> ")
        nav_items = []
        for unit_slug, data in sorted_units:
            unit_title = data["title"] or unit_slug
            nav_items.append(f'<a href="#{unit_slug}">{unit_title}</a>')
        lines.append(" | ".join(nav_items))
        lines.append("</nav>")

    for unit_slug, data in sorted_units:
        lines.append(f'<h2 id="{unit_slug}">{data["title"]}</h2>')
        lessons = data["lessons"]
        if lessons:
            lines.append("<ul>")
            for t,u,_ in lessons:
                safe = (t or u).replace("<","&lt;").replace(">","&gt;")
                lines.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
            lines.append("</ul>")
        else:
            lines.append("<p><em>No lessons discovered for this unit.</em></p>")

    lines.append(f"<p>Total lessons discovered: {total}</p>")
    lines.extend(["</body></html>"])
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {total} lessons grouped into {len(sorted_units)} units.")

def main():
    print(f"Starting generator. API_KEY present: {bool(API_KEY)}; playwright available: {PLAYWRIGHT_AVAILABLE}")
    build_index()

if __name__ == "__main__":
    main()
