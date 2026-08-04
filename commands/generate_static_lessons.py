#!/usr/bin/env python3
"""
Programme-level scraper + canonicalizer.

- Scrapes the teacher programme overview page:
    https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}
  to find all teacher lesson links.
- Falls back to Playwright rendering of that page if needed.
- Maps unit slugs to unit titles using the Open API /sequences/{seq}/units call.
- Resolves canonical lesson metadata via /lessons/{lessonSlug}/summary when available.
- Writes site/index.html grouping lessons by unit title and using canonical /lessons/... URLs.
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
TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-ks3")

API_BASE = "https://open-api.thenational.academy/api/v0"
TEACHER_PROG_BASE = f"https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}"

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

UNIT_SLUG_RE = re.compile(r"/teachers/programmes/[^/]+/units/([^/]+)/lessons/([^/?#]+)")

def get_json(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if "application/json" in r.headers.get("content-type", ""):
            return r.status_code, r.json()
        return r.status_code, None
    except Exception as e:
        print("JSON request failed:", e)
        return 0, None

def get_html(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": "oak-scraper/1.0"}, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        print("HTML request failed:", e)
        return 0, ""

def fetch_units_map(sequence_slug: str) -> Dict[str,str]:
    """Return mapping unitSlug -> unitTitle using the API."""
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
        # normalize relative hrefs
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
        print("Playwright error:", e)
        return []
    return parse_lesson_links_from_html(content)

def fetch_canonical_summary(lesson_slug: str) -> Tuple[int, Dict[str,Any]]:
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    status, j = get_json(url)
    return status, j if isinstance(j, dict) else {}

def slug_from_teacher_lesson_url(url: str) -> Tuple[str,str]:
    # return (unit_slug, lesson_slug)
    m = UNIT_SLUG_RE.search(urlparse(url).path)
    if m:
        return m.group(1), m.group(2)
    # fallback: last segment as lesson slug
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 1:
        return (parts[-3] if len(parts) >= 3 else "", parts[-1])
    return "", url

def main():
    print(f"Starting programme-level scrape: {TEACHER_PROG_BASE}")
    unit_map = fetch_units_map(API_SEQ)

    status, html = get_html(TEACHER_PROG_BASE)
    links: List[Tuple[str,str]] = []
    if status == 200 and html:
        links = parse_lesson_links_from_html(html)
        print(f"Found {len(links)} lesson links via requests on programme page.")
    if not links:
        print("No links from requests; trying Playwright render of the programme page.")
        links = playwright_render_and_parse(TEACHER_PROG_BASE)
        print(f"Found {len(links)} lesson links via Playwright render.")

    # group by unit slug
    grouped: Dict[str, Dict[str,Any]] = {}  # unit_slug -> {title, lessons: [(title,url,lesson_slug)]}
    for title, href in links:
        unit_slug, lesson_slug = slug_from_teacher_lesson_url(href)
        if not lesson_slug:
            continue
        # prefer canonical API summary
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
        time.sleep(0.05)

    # sort units by title
    sorted_units = sorted(grouped.items(), key=lambda it: it[1]["title"] or it[0])
    total = sum(len(v["lessons"]) for _, v in sorted_units)
    print(f"Collected {total} lessons across {len(sorted_units)} units.")

    # write grouped HTML
    out_lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} h2{margin-top:1.25rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — programme: {TEACHER_SEQ}</h1>",
        "<p>Automatically generated. Lessons grouped by unit. Canonical lesson pages used when available.</p>",
    ]
    for unit_slug, data in sorted_units:
        out_lines.append(f'<h2 id="{unit_slug}">{data["title"]}</h2>')
        if data["lessons"]:
            out_lines.append("<ul>")
            for t,u,_ in data["lessons"]:
                safe = (t or u).replace("<","&lt;").replace(">","&gt;")
                out_lines.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
            out_lines.append("</ul>")
        else:
            out_lines.append("<p><em>No lessons discovered for this unit.</em></p>")

    out_lines.append(f"<p>Total lessons discovered: {total}</p>")
    out_lines.extend(["</body></html>"])
    OUT_PATH.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {total} lessons grouped into {len(sorted_units)} units.")

if __name__ == "__main__":
    main()
