#!/usr/bin/env python3
"""
Fetch programme -> units -> lessons from the Oak API and write a simple
site/index.html containing an unordered list of lesson links.

This script expects the API key in the OAK_API_KEY environment variable.
If no API key is present it will still attempt unauthenticated requests.
Adjust BASE_URL if your API uses a different hostname.
"""
from __future__ import annotations
import os
import sys
import requests
from pathlib import Path

BASE_URL = os.environ.get("OAK_API_BASE", "https://api.thenational.academy")  # change if needed
API_KEY = os.environ.get("OAK_API_KEY")

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "oak-static-export/0.1",
}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"


def get_json(path, params=None):
    url = path if path.startswith("http") else f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Request failed for {url}: {e}", file=sys.stderr)
        return None


def main():
    # 1. Find the programme (try canonical AQA secondary slug first)
    #    If you prefer a different programme change this logic.
    programme_slug = "science-secondary-aqa"

    print("Fetching programmes list...")
    progs = get_json("/programmes?exam_board=aqa&phase=secondary")
    if progs:
        # try to detect canonical slug in response
        results = progs.get("results") if isinstance(progs, dict) else progs
        if results:
            for p in results:
                if p.get("slug") == programme_slug:
                    print("Found programme in list.")
                    break
            else:
                # fallback: pick first programme if slug isn't present
                if isinstance(results, list) and len(results) > 0:
                    programme_slug = results[0].get("slug") or programme_slug
                    print(f"Using first programme slug: {programme_slug}")

    # 2. Fetch units for programme
    print(f"Fetching units for programme {programme_slug} ...")
    units_json = get_json(f"/programmes/{programme_slug}/units")
    units = []
    if isinstance(units_json, dict):
        units = units_json.get("results") or units_json.get("units") or units_json.get("data") or []
    elif isinstance(units_json, list):
        units = units_json
    units = units or []

    lesson_links = []  # list of (title, url)
    for u in units:
        unit_slug = u.get("slug") or u.get("id")
        if not unit_slug:
            continue
        print(f"  Fetching lessons for unit {unit_slug} ...")
        unit_detail = get_json(f"/units/{unit_slug}")
        lessons = []
        if isinstance(unit_detail, dict):
            lessons = unit_detail.get("lessons") or unit_detail.get("results") or unit_detail.get("items") or unit_detail.get("data") or []
        elif isinstance(unit_detail, list):
            lessons = unit_detail
        for l in lessons:
            title = l.get("title") or l.get("name") or l.get("slug") or "Untitled"
            url = l.get("url") or l.get("link") or l.get("path")
            if url and url.startswith("/"):
                # make absolute using public site if needed
                url = f"https://www.thenational.academy{url}"
            if url:
                lesson_links.append((title, url))
            else:
                # As a fallback, if the lesson has a slug we can construct a plausible URL
                lslug = l.get("slug") or l.get("id")
                if lslug:
                    lesson_links.append((title, f"https://www.thenational.academy/lessons/{lslug}"))

    # If nothing collected, try a brute-force approach (optional): 
    if not lesson_links:
        print("No lessons found using programme/unit endpoints; attempting a broad search of programme units output.")
        # Try to parse units_json for embedded lessons
        for u in units:
            for key in ("lessons", "items", "data"):
                for l in (u.get(key) or []):
                    title = l.get("title") or l.get("name") or l.get("slug") or "Untitled"
                    url = l.get("url") or l.get("link") or l.get("path")
                    if url and url.startswith("/"):
                        url = f"https://www.thenational.academy{url}"
                    if url:
                        lesson_links.append((title, url))

    # 3. Write a simple static HTML page
    print(f"Writing {OUT_PATH} with {len(lesson_links)} links...")
    html_lines = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} a{color:#0366d6}</style>",
        "</head><body>",
        "<h1>Oak lesson links</h1>",
        "<p>Automatically generated. If links look wrong, update the API mapping in the generator script.</p>",
        "<ul>"
    ]
    for title, url in lesson_links:
        safe_title = (title or "").replace("<", "&lt;").replace(">", "&gt;")
        html_lines.append(f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{safe_title}</a></li>")
    html_lines.extend(["</ul>", "</body></html>"])
    OUT_PATH.write_text("\n".join(html_lines), encoding="utf-8")
    print("Done.")


if __name__ == "__main__":
    main()
