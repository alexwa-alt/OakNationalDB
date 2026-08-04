#!/usr/bin/env python3
"""
Generator that:
- fetches units from the Open API sequence (API sequence slug),
- fetches the corresponding teacher unit pages (teacher sequence slug),
- scrapes lesson links from those pages,
- validates/fetches lesson metadata from Open API /lessons/{lessonSlug}/summary,
- writes site/index.html with the discovered lesson links.

Environment:
- OAK_API_KEY (optional but recommended) -> used for lesson summary calls
- OAK_API_SEQUENCE (optional) -> API sequence slug (default: science-secondary-aqa)
- OAK_TEACHER_SEQUENCE (optional) -> teacher site sequence slug used in URLs (default: science-secondary-ks3)
"""
from __future__ import annotations
import os
import sys
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
TEACHER_BASE = "https://www.thenational.academy/teachers/programmes"

HEADERS = {"Accept": "application/json", "User-Agent": "oak-static-generator/1.0"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"
REQUEST_TIMEOUT = 15

def get_json(url: str) -> Tuple[int, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.json() if r.headers.get("content-type","").startswith("application/json") else None
    except Exception as e:
        print(f"JSON GET error for {url}: {e}", file=sys.stderr)
        return 0, None

def get_html(url: str) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers={"User-Agent": "oak-scraper/1.0"}, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        print(f"HTML GET error for {url}: {e}", file=sys.stderr)
        return 0, ""

def fetch_units(api_sequence_slug: str) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sequences/{api_sequence_slug}/units"
    print(f"Fetching units from API: {url}")
    status, j = get_json(url)
    if status != 200 or j is None:
        print(f"Failed to fetch units (status {status}).", file=sys.stderr)
        return []
    units: List[Dict[str, Any]] = []
    if isinstance(j, list):
        for entry in j:
            if isinstance(entry, dict) and entry.get("units"):
                for u in entry["units"]:
                    if isinstance(u, dict):
                        units.append(u)
            else:
                # maybe list contains units directly
                if isinstance(entry, dict) and ("unitSlug" in entry or "slug" in entry or "id" in entry):
                    units.append(entry)
    elif isinstance(j, dict) and j.get("units"):
        units = j["units"]
    print(f"Found {len(units)} units in API response.")
    return units

def scrape_teacher_unit_for_lessons(teacher_sequence: str, unit_slug: str) -> List[Tuple[str,str]]:
    # teacher unit URL pattern from your examples:
    # https://www.thenational.academy/teachers/programmes/{teacher_sequence}/units/{unit_slug}
    unit_url = f"{TEACHER_BASE}/{teacher_sequence}/units/{unit_slug}"
    print(f"  Fetching teacher unit page: {unit_url}")
    status, html = get_html(unit_url)
    if status != 200 or not html:
        print(f"   -> Teacher page returned {status}; skipping scrape.")
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: Set[Tuple[str,str]] = set()
    # find anchors containing '/lessons/' under the teacher path
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/lessons/" in href:
            # make absolute if needed
            parsed = urlparse(href)
            if not parsed.netloc:
                href = urljoin(TEACHER_BASE, href) if href.startswith("/") else urljoin(unit_url, href)
            title = a.get_text(strip=True) or href
            links.add((title, href))
    # Normalize and return sorted list
    result = sorted(list(links), key=lambda x: x[1])
    print(f"   -> Scraped {len(result)} lesson link(s) from teacher page.")
    return result

def fetch_lesson_summary(lesson_slug: str) -> Tuple[int, Dict[str,Any]]:
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    status, j = get_json(url)
    return status, j if isinstance(j, dict) else {}

def lesson_slug_from_teacher_url(url: str) -> str:
    # teacher lesson URL example:
    # /teachers/programmes/science-secondary-ks3/units/forces/lessons/what-forces-do
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    # lesson slug should be last segment
    return parts[-1] if parts else url

def main():
    print(f"Starting generator. API_KEY present: {bool(API_KEY)}")
    units = fetch_units(API_SEQ)
    all_links: List[Tuple[str,str]] = []
    seen_urls: Set[str] = set()

    for u in units:
        unit_slug = u.get("unitSlug") or u.get("slug") or u.get("unit_slug") or u.get("id")
        unit_title = u.get("unitTitle") or u.get("title") or u.get("unitTitle") or unit_slug
        if not unit_slug:
            continue
        print(f"Processing unit: {unit_title} (slug: {unit_slug})")
        scraped = scrape_teacher_unit_for_lessons(TEACHER_SEQ, unit_slug)
        if not scraped:
            # fallback: try teacher unit URL with /units/{unit_slug} + /lessons path
            fallback_unit_lessons = f"{TEACHER_BASE}/{TEACHER_SEQ}/units/{unit_slug}/lessons"
            status, html = get_html(fallback_unit_lessons)
            if status == 200 and html:
                # parse links if present
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/lessons/" in href:
                        if not urlparse(href).netloc:
                            href = urljoin(fallback_unit_lessons, href)
                        scraped.append((a.get_text(strip=True) or href, href))

        # For each scraped link, validate / enrich with API summary if possible
        for title, href in scraped:
            lesson_slug = lesson_slug_from_teacher_url(href)
            # prefer teacher URL as final link
            final_url = href
            # try to fetch summary from API
            status, summary = fetch_lesson_summary(lesson_slug)
            if status == 200 and summary:
                canonical_title = summary.get("lessonTitle") or summary.get("title") or title
                # use canonical teacher URL if available; else fall back to https://www.thenational.academy/lessons/{lesson_slug}
                if "/teachers/" not in final_url:
                    final_url = f"https://www.thenational.academy/lessons/{lesson_slug}"
                title = canonical_title or title
            else:
                # if API doesn't return summary, try public /lessons/{slug} page
                if "/teachers/" not in final_url:
                    final_url = f"https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}/units/{unit_slug}/lessons/{lesson_slug}"
            if final_url not in seen_urls:
                seen_urls.add(final_url)
                all_links.append((title, final_url))
        # small delay to be polite
        time.sleep(0.2)

    print(f"Total lesson links discovered: {len(all_links)}")

    # Write site/index.html
    html_lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — sequence: {TEACHER_SEQ} (scraped)</h1>",
        "<p>Automatically generated. Links discovered:</p>",
        "<ul>",
    ]
    if all_links:
        for t, u in all_links:
            safe = (t or u).replace("<","&lt;").replace(">","&gt;")
            html_lines.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
    else:
        html_lines.append("<li>No links discovered. See Actions logs for details.</li>")
    html_lines.extend(["</ul>", "</body></html>"])
    OUT_PATH.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {len(all_links)} links.")

if __name__ == "__main__":
    main()
