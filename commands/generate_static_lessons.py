#!/usr/bin/env python3
"""
Static lesson URL generator (requests + __NEXT_DATA__ parsing, no Playwright).

- Reads OAK_API_KEY, OAK_API_SEQUENCE, OAK_TEACHER_SEQUENCE from the environment.
- Fetches the teacher programme "/units" page.
- Tries:
  1) to extract lesson links from a server-rendered __NEXT_DATA__ JSON blob if present
  2) to scrape <a> anchors that contain "/teachers/programmes/" and "/lessons/"
- Canonicalises each lesson using the Open API /lessons/{slug}/summary when OAK_API_KEY is provided.
- Groups lessons by unit slug and writes site/index.html.
"""
from __future__ import annotations
import os
import re
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set
import requests
from bs4 import BeautifulSoup

API_BASE = "https://open-api.thenational.academy/api/v0"
TEACHER_BASE = "https://www.thenational.academy/teachers/programmes"

API_KEY = os.environ.get("OAK_API_KEY")
API_SEQ = os.environ.get("OAK_API_SEQUENCE", "science-secondary-aqa")
TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-aqa")
TEACHER_UNITS_URL = f"{TEACHER_BASE}/{TEACHER_SEQ}/units"

HEADERS_REQ = {"User-Agent": "oak-static-generator/1.0"}
HEADERS_API = {"Accept": "application/json", "User-Agent": "oak-static-generator/1.0"}
if API_KEY:
    HEADERS_API["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"

LESSON_URL_RE = re.compile(r"/teachers/programmes/[^/]+/units/([^/]+)/lessons/([^/?#]+)")
LESSON_SLUG_RE = re.compile(r"/lessons/([a-z0-9\-_\.]+)", re.I)

def fetch_html(url: str, timeout: int = 15) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers=HEADERS_REQ, timeout=timeout)
        return r.status_code, r.text or ""
    except Exception as e:
        return 0, f"EXCEPTION: {e}"

def get_json(url: str, headers: Dict[str, str] = None, timeout: int = 12):
    try:
        r = requests.get(url, headers=headers or HEADERS_API, timeout=timeout)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "application/json" in ct:
            return r.status_code, r.json()
        return r.status_code, None
    except Exception as e:
        print("JSON request failed for", url, ":", e)
        return 0, None

def extract_next_data_from_html(html: str) -> Any:
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, flags=re.S | re.I)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        # sometimes the content is escaped or truncated; try a best-effort substring parse
        try:
            return json.loads(raw[:400000])
        except Exception:
            return None

def extract_lesson_links_from_nextdata(obj: Any) -> List[Tuple[str, str]]:
    found: Set[Tuple[str,str]] = set()
    if obj is None:
        return []
    # Search strings / objects recursively for lesson links or slugs
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str):
                    for m in re.finditer(r"/teachers/programmes/[^\"'\s>]*?/lessons/([a-z0-9\-_\.]+)", v, flags=re.I):
                        slug = m.group(1)
                        url = f"https://www.thenational.academy/lessons/{slug}"
                        found.add((slug, url))
                    # also capture canonical short /lessons/ forms
                    for m in LESSON_SLUG_RE.finditer(v):
                        slug = m.group(1)
                        found.add((slug, f"https://www.thenational.academy/lessons/{slug}"))
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)
        elif isinstance(x, str):
            for m in re.finditer(r"/teachers/programmes/[^\"'\s>]*?/lessons/([a-z0-9\-_\.]+)", x, flags=re.I):
                slug = m.group(1)
                found.add((slug, f"https://www.thenational.academy/lessons/{slug}"))
            for m in LESSON_SLUG_RE.finditer(x):
                slug = m.group(1)
                found.add((slug, f"https://www.thenational.academy/lessons/{slug}"))
    walk(obj)
    return sorted(found, key=lambda x: x[0])

def parse_lesson_links_from_html(html: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: Set[Tuple[str,str]] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = requests.compat.urljoin("https://www.thenational.academy", href)
        if "/teachers/programmes/" in href and "/lessons/" in href:
            title = a.get_text(strip=True) or href
            found.add((title, href))
    # try also searching raw HTML for lesson URL patterns
    for m in re.finditer(r'href=["\']([^"\']*?/teachers/programmes/[^"\']*?/lessons/([^"\']+))["\']', html, flags=re.I):
        href = m.group(1)
        title = ""
        if href.startswith("/"):
            href = requests.compat.urljoin("https://www.thenational.academy", href)
        found.add((title, href))
    return sorted(found, key=lambda x: x[1])

def unit_lesson_from_teacher_url(url: str) -> Tuple[str, str]:
    m = LESSON_URL_RE.search(url)
    if m:
        return m.group(1), m.group(2)
    # fallback heuristics
    parts = [p for p in requests.compat.urlparse(url).path.split("/") if p]
    if "lessons" in parts:
        idx = parts.index("lessons")
        lesson = parts[idx+1] if idx+1 < len(parts) else ""
        unit = parts[idx-2] if idx-2 >= 0 else ""
        return unit, lesson
    # if canonical /lessons/ URL, extract slug
    m2 = LESSON_SLUG_RE.search(url)
    return "", m2.group(1) if m2 else ""

def fetch_units_map(sequence_slug: str) -> Dict[str, str]:
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
                if isinstance(entry, dict):
                    slug = entry.get("unitSlug") or entry.get("slug") or entry.get("id")
                    title = entry.get("unitTitle") or entry.get("title") or slug
                    if slug:
                        mapping[slug] = title
    else:
        print(f"Warning: failed to fetch units map from API (status {status}). Unit titles may be missing.")
    return mapping

def fetch_lesson_summary(lesson_slug: str):
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    status, j = get_json(url)
    return status, j if isinstance(j, dict) else {}

def build_index():
    print(f"Starting generator. TEACHER_SEQ={TEACHER_SEQ}; API_SEQ={API_SEQ}; API_KEY present: {bool(API_KEY)}")
    unit_map = fetch_units_map(API_SEQ)

    status, html = fetch_html(TEACHER_UNITS_URL)
    links: List[Tuple[str,str]] = []
    if status == 200 and html:
        # 1) try __NEXT_DATA__
        nextdata = extract_next_data_from_html(html)
        if nextdata:
            print("__NEXT_DATA__ found - extracting lesson links from it")
            links = extract_lesson_links_from_nextdata(nextdata)
            links = [(slug, url) for slug, url in links]
        if not links:
            print("No lesson links found in __NEXT_DATA__ - scraping anchors")
            links = parse_lesson_links_from_html(html)
            # reduce anchor titles when they are long
            links = [(t[:200] if isinstance(t,str) else t, u) for t,u in links]
        print(f"Discovered {len(links)} raw lesson links from the programme page (status {status}).")
    else:
        print("Failed to fetch programme page or empty body; status:", status)

    grouped: Dict[str, Dict[str, Any]] = {}
    seen_urls: Set[str] = set()

    for title, href in links:
        # normalize href to canonical lesson URL when possible
        unit_slug, lesson_slug = unit_lesson_from_teacher_url(href)
        if not lesson_slug:
            continue
        canonical_title = title or ""
        canonical_url = href
        if API_KEY:
            st, summary = fetch_lesson_summary(lesson_slug)
            if st == 200 and summary:
                canonical_title = summary.get("lessonTitle") or summary.get("title") or canonical_title
                canonical_url = f"https://www.thenational.academy/lessons/{lesson_slug}"
        unit_title = unit_map.get(unit_slug, unit_slug or "Unmapped unit")
        if unit_slug not in grouped:
            grouped[unit_slug] = {"title": unit_title, "lessons": []}
        if canonical_url in seen_urls:
            continue
        grouped[unit_slug]["lessons"].append((canonical_title or canonical_url, canonical_url, lesson_slug))
        seen_urls.add(canonical_url)
        time.sleep(0.02)

    sorted_units = sorted(grouped.items(), key=lambda it: (it[1]["title"] or it[0]).lower())
    total = sum(len(v["lessons"]) for _, v in sorted_units)
    print(f"Collected {total} lessons across {len(sorted_units)} units.")

    # write simple HTML
    lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Oak lesson links — programme: {TEACHER_SEQ}</title>",
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

if __name__ == "__main__":
    build_index()
