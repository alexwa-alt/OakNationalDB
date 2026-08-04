#!/usr/bin/env python3
"""
Generator that:
- fetches units from the Open API sequence (API sequence slug),
- scrapes teacher unit pages for lesson links (requests + BeautifulSoup),
- falls back to Playwright if requests scraping finds no links,
- fetches canonical lesson summary from API (/lessons/{lessonSlug}/summary) when available,
- writes site/index.html grouping lessons by unit and using canonical lesson pages where possible.

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

# Config from env
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
PLAYWRIGHT_AVAILABLE = False

# Try to import playwright but don't fail at import time; we'll import when needed.
try:
    # only used if fallback required; leave import attempt to runtime to avoid unnecessary deps errors
    from playwright.sync_api import sync_playwright  # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

def get_json(url: str) -> Tuple[int, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.status_code, r.json()
        else:
            # still return body as text under None JSON
            return r.status_code, None
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
                if isinstance(entry, dict) and ("unitSlug" in entry or "slug" in entry or "id" in entry):
                    units.append(entry)
    elif isinstance(j, dict) and j.get("units"):
        units = j["units"]
    print(f"Found {len(units)} units in API response.")
    return units

def lesson_slug_from_url(url: str) -> str:
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    return parts[-1] if parts else ""

def scrape_teacher_unit_for_lessons_requests(teacher_sequence: str, unit_slug: str) -> List[Tuple[str,str]]:
    unit_url = f"{TEACHER_BASE}/{teacher_sequence}/units/{unit_slug}"
    print(f"  [requests] Fetching teacher unit page: {unit_url}")
    status, html = get_html(unit_url)
    if status != 200 or not html:
        print(f"   -> Teacher page returned {status}; requests scrape found nothing.")
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: Set[Tuple[str,str]] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/lessons/" in href:
            if not urlparse(href).netloc:
                # make absolute relative to teacher site
                if href.startswith("/"):
                    href = urljoin("https://www.thenational.academy", href)
                else:
                    href = urljoin(unit_url, href)
            title = a.get_text(strip=True) or href
            links.add((title, href))
    result = sorted(list(links), key=lambda x: x[1])
    print(f"   -> requests scrape found {len(result)} lesson link(s).")
    return result

def scrape_teacher_unit_for_lessons_playwright(teacher_sequence: str, unit_slug: str) -> List[Tuple[str,str]]:
    if not PLAYWRIGHT_AVAILABLE:
        print("   -> Playwright not available in environment; cannot run fallback.", file=sys.stderr)
        return []
    unit_url = f"{TEACHER_BASE}/{teacher_sequence}/units/{unit_slug}"
    print(f"  [playwright] Rendering teacher unit page: {unit_url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(unit_url, timeout=30000)
            content = page.content()
            browser.close()
    except Exception as e:
        print(f"   -> Playwright navigation error for {unit_url}: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(content, "html.parser")
    links: Set[Tuple[str,str]] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/lessons/" in href:
            if not urlparse(href).netloc:
                if href.startswith("/"):
                    href = urljoin("https://www.thenational.academy", href)
                else:
                    href = urljoin(unit_url, href)
            title = a.get_text(strip=True) or href
            links.add((title, href))
    result = sorted(list(links), key=lambda x: x[1])
    print(f"   -> playwright scrape found {len(result)} lesson link(s).")
    return result

def fetch_lesson_summary(lesson_slug: str) -> Tuple[int, Dict[str,Any]]:
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    status, j = get_json(url)
    return status, j if isinstance(j, dict) else {}

def build_index(api_sequence: str, teacher_sequence: str) -> None:
    units = fetch_units(api_sequence)
    grouped: List[Tuple[str, str, List[Tuple[str,str]]]] = []  # (unitTitle, unitSlug, [(title,url)])
    total_links = 0

    for u in units:
        unit_slug = u.get("unitSlug") or u.get("slug") or u.get("unit_slug") or u.get("id")
        unit_title = u.get("unitTitle") or u.get("title") or u.get("unitTitle") or unit_slug
        if not unit_slug:
            continue
        print(f"Processing unit: {unit_title} (slug: {unit_slug})")
        links = scrape_teacher_unit_for_lessons_requests(teacher_sequence, unit_slug)
        used_playwright = False
        if not links:
            # try Playwright fallback if available; otherwise we will keep teacher-less
            links = scrape_teacher_unit_for_lessons_playwright(teacher_sequence, unit_slug)
            used_playwright = True if links else False
        # Extract slugs and canonicalize links via API summary where possible
        final_links: List[Tuple[str,str]] = []
        seen_urls: Set[str] = set()
        for title, href in links:
            lesson_slug = lesson_slug_from_url(href)
            canonical_url = f"https://www.thenational.academy/lessons/{lesson_slug}"
            status, summary = fetch_lesson_summary(lesson_slug)
            if status == 200 and summary:
                canonical_title = summary.get("lessonTitle") or summary.get("title") or title
                final_url = canonical_url
                final_title = canonical_title or title
            else:
                # API didn't return summary; fall back to teacher link (keep original href)
                final_url = href
                final_title = title
            if final_url not in seen_urls:
                seen_urls.add(final_url)
                final_links.append((final_title, final_url))
        print(f"  -> unit collected {len(final_links)} lessons (playwright fallback used: {used_playwright})")
        total_links += len(final_links)
        grouped.append((unit_title, unit_slug, final_links))
        time.sleep(0.15)

    # Write grouped HTML
    lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} h2{margin-top:1.25rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — sequence: {teacher_sequence}</h1>",
        "<p>Automatically generated. Lessons grouped by unit. Canonical lesson pages used when available.</p>",
    ]
    for unit_title, unit_slug, lessons in grouped:
        lines.append(f"<h2>{unit_title}</h2>")
        if lessons:
            lines.append("<ul>")
            for t, u in lessons:
                safe = (t or u).replace("<", "&lt;").replace(">", "&gt;")
                lines.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
            lines.append("</ul>")
        else:
            lines.append("<p><em>No lessons discovered for this unit.</em></p>")

    lines.append(f"<p>Total lessons discovered: {total_links}</p>")
    lines.extend(["</body></html>"])
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {total_links} links across {len(grouped)} units.")

def main():
    print(f"Starting generator. API_KEY present: {bool(API_KEY)}; playwright available: {PLAYWRIGHT_AVAILABLE}")
    build_index(API_SEQ, TEACHER_SEQ)

if __name__ == "__main__":
    main()
