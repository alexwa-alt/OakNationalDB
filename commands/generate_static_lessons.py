#!/usr/bin/env python3
"""
Extractor that reads sequence units from the Oak Open API and finds lesson URLs.

How it works:
- Uses the Open API base: https://open-api.thenational.academy/api/v0
- Fetches /sequences/{slug}/units, iterates each unit and tries:
    * Look for embedded 'lessons' in the unit object
    * GET /units/{unitSlug}
    * GET /units/{unitSlug}/lessons
  and extracts lesson slug/url/title from any successful response.
- Writes site/index.html with discovered lesson links and prints clear debug lines.

Set OAK_PROGRAMME_SLUG to a different sequence if desired.
"""
from __future__ import annotations
import os
import sys
import json
import requests
from pathlib import Path
from typing import Any, Dict, List, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-extractor/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

BASE = "https://open-api.thenational.academy/api/v0"
PROGRAMME_SLUG = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")

OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"
TIMEOUT = 15

def get_json(url: str) -> Tuple[int, Any]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        status = r.status_code
        try:
            j = r.json()
        except Exception:
            j = None
        return status, j
    except Exception as e:
        print(f"GET EXCEPTION for {url}: {e}", file=sys.stderr)
        return 0, None

def extract_lesson_candidates_from_obj(obj: Any) -> List[Tuple[str,str]]:
    """Find lesson-like items inside dict/list structures and return (title,url)."""
    out: List[Tuple[str,str]] = []
    if isinstance(obj, dict):
        # If object itself looks like a lesson
        for key in ("url","link","path","publicUrl","public_url","asset_url"):
            v = obj.get(key)
            if isinstance(v, str) and v:
                url = v
                if url.startswith("/"):
                    url = "https://www.thenational.academy" + url
                title = obj.get("title") or obj.get("name") or obj.get("slug") or ""
                out.append((title, url))
        # Common lesson lists
        for k in ("lessons","items","results","data"):
            v = obj.get(k)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        slug = it.get("slug") or it.get("id") or it.get("lessonSlug") or it.get("lesson_slug")
                        title = it.get("title") or it.get("name") or slug or ""
                        # prefer explicit URL fields
                        for key in ("url","link","path","publicUrl","public_url"):
                            u = it.get(key)
                            if isinstance(u, str) and u:
                                uu = u
                                if uu.startswith("/"):
                                    uu = "https://www.thenational.academy" + uu
                                out.append((title, uu))
                        # fallback: construct public lesson URL from slug if present
                        if slug:
                            out.append((title, f"https://www.thenational.academy/lessons/{slug}"))
        # recurse deeper
        for v in obj.values():
            if isinstance(v, (list, dict)):
                out.extend(extract_lesson_candidates_from_obj(v))
    elif isinstance(obj, list):
        for it in obj:
            out.extend(extract_lesson_candidates_from_obj(it))
    return out

def try_unit_endpoints(unit_slug: str) -> List[Tuple[str,str]]:
    """Try several unit endpoints to find lessons."""
    found: List[Tuple[str,str]] = []
    candidates = [
        f"{BASE}/units/{unit_slug}",
        f"{BASE}/units/{unit_slug}/lessons",
        f"{BASE}/units/{unit_slug}?include=lessons",
    ]
    for url in candidates:
        status, j = get_json(url)
        print(f"  Tried {url} -> status {status}")
        if j is not None:
            links = extract_lesson_candidates_from_obj(j)
            if links:
                print(f"    -> Found {len(links)} lesson candidates at {url}")
                found.extend(links)
                break
    return found

def main():
    print(f"Starting extractor for sequence: {PROGRAMME_SLUG}. API_KEY present: {bool(API_KEY)}")

    seq_units_url = f"{BASE}/sequences/{PROGRAMME_SLUG}/units"
    status, j = get_json(seq_units_url)
    print(f"Fetched sequence units: {seq_units_url} -> status {status}")
    if status != 200 or j is None:
        print("Failed to fetch sequence units or no JSON returned. Aborting.", file=sys.stderr)
        # Write empty placeholder page
        OUT_PATH.write_text("<html><body><h1>No links - sequence units fetch failed</h1></body></html>", encoding="utf-8")
        return

    # The response is likely a list of year entries, each containing 'units'
    discovered: List[Tuple[str,str]] = []

    if isinstance(j, list):
        # iterate year entries
        for year_entry in j:
            units = year_entry.get("units") if isinstance(year_entry, dict) else None
            if not units:
                # maybe the list itself is units
                if isinstance(year_entry, dict) and "slug" in year_entry:
                    units = [year_entry]
            if not units:
                continue
            for u in units:
                # try to identify unit slug/id/title
                if not isinstance(u, dict):
                    continue
                unit_slug = u.get("unitSlug") or u.get("slug") or u.get("id") or u.get("unit_slug") or u.get("unit_slug")
                unit_title = u.get("title") or u.get("unitTitle") or u.get("name") or unit_slug
                print(f"Processing unit: {unit_title} (slug/id: {unit_slug})")
                # first, see if unit object includes lesson info
                embedded = extract_lesson_candidates_from_obj(u)
                if embedded:
                    print(f"  -> Found {len(embedded)} embedded lesson candidates in unit object")
                    discovered.extend(embedded)
                    continue
                # try endpoints using the slug/id
                if unit_slug:
                    links = try_unit_endpoints(unit_slug)
                    if links:
                        discovered.extend(links)
                        continue
                # fallback: try to find lessons by scanning unit's keys
                # (already covered by extract_lesson_candidates_from_obj recursing)
    elif isinstance(j, dict):
        # Unusual shape: try to extract lessons directly
        print("Sequence units response was a dict; attempting to scan for lessons")
        discovered.extend(extract_lesson_candidates_from_obj(j))

    # Deduplicate and clean
    seen = set()
    deduped: List[Tuple[str,str]] = []
    for title, url in discovered:
        if not url:
            continue
        url = url.split("#")[0]
        if url not in seen:
            seen.add(url)
            deduped.append((title or url, url))

    print(f"Total unique lesson links discovered: {len(deduped)}")

    # Write final HTML page
    lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — sequence: {PROGRAMME_SLUG}</h1>",
        "<p>Automatically generated. Links discovered:</p>",
        "<ul>",
    ]
    if deduped:
        for t, u in deduped:
            safe = (t or u).replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
    else:
        lines.append("<li>No links discovered. Check Actions logs for diagnostics.</li>")
    lines.extend(["</ul>", "</body></html>"])
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {len(deduped)} links.")

if __name__ == "__main__":
    main()
