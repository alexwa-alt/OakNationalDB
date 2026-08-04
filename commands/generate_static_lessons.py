#!/usr/bin/env python3
"""
Targeted debug + extractor for Oak Open API (open-api.thenational.academy/api/v0).

This script:
- tries a small list of realistic API endpoints under the official Open API host,
- prints HTTP status and a short JSON preview for each request,
- attempts to extract lesson slugs/URLs and writes site/index.html with any links found.

After the workflow runs, paste the full output from the "Run generator to build site/index.html"
step here and I will convert the successful path into a minimal production extractor.
"""
from __future__ import annotations
import os, sys, json, requests
from pathlib import Path
from typing import Any, Tuple, List

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-openapi-debug/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"

BASE = "https://open-api.thenational.academy/api/v0"
PROGRAMME_SLUG = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")

CANDIDATES = [
    "/sequences",
    "/sequences?subject=science",
    "/sequences?exam_board=aqa",
    f"/sequences/{PROGRAMME_SLUG}",
    f"/sequences/{PROGRAMME_SLUG}/units",
    "/programmes",
    f"/programmes/{PROGRAMME_SLUG}/units",
    "/units",
    f"/units?sequence={PROGRAMME_SLUG}",
    "/lessons",
]

def get_json(url: str) -> Tuple[int, Any, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        status = r.status_code
        text_preview = (r.text[:800] + "...") if len(r.text) > 800 else r.text
        try:
            j = r.json()
        except Exception:
            j = None
        return status, j, text_preview
    except Exception as e:
        return 0, None, f"EXCEPTION: {e}"

def scan_for_lesson_urls(obj: Any) -> List[Tuple[str,str]]:
    out: List[Tuple[str,str]] = []
    if isinstance(obj, dict):
        # common keys likely to contain lesson lists
        for k in ("lessons", "results", "items", "data", "units"):
            v = obj.get(k)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        slug = it.get("slug") or it.get("id")
                        title = it.get("title") or it.get("name") or slug
                        # attempt to build public lesson URL if slug looks like a lesson slug
                        if slug and "lesson" in (it.get("type","") or "").lower():
                            out.append((title, f"https://www.thenational.academy/lessons/{slug}"))
                        # if the object itself looks like a lesson (has 'link'/'url')
                        for key in ("url","link","path","public_url"):
                            u = it.get(key)
                            if isinstance(u, str) and u:
                                if u.startswith("/"):
                                    u = "https://www.thenational.academy" + u
                                out.append((title, u))
        # also check direct fields for a lesson-like object
        for key in ("url","link","path","public_url"):
            v = obj.get(key)
            if isinstance(v, str) and v:
                out.append((obj.get("title") or obj.get("name") or "", v))
        # recurse
        for v in obj.values():
            if isinstance(v, (list, dict)):
                out.extend(scan_for_lesson_urls(v))
    elif isinstance(obj, list):
        for it in obj:
            out.extend(scan_for_lesson_urls(it))
    return out

def main():
    print("Debug run against Open API host. API_KEY provided:", bool(API_KEY))
    discovered: List[Tuple[str,str]] = []
    for path in CANDIDATES:
        url = BASE.rstrip("/") + "/" + path.lstrip("/")
        print("\n--- REQUEST ->", url)
        status, j, preview = get_json(url)
        print("status:", status)
        if j is not None:
            print("json type:", type(j).__name__)
            if isinstance(j, dict):
                print("keys:", list(j.keys())[:15])
            elif isinstance(j, list):
                print("list length:", len(j))
                if len(j) > 0 and isinstance(j[0], dict):
                    print("first item keys:", list(j[0].keys())[:15])
            links = scan_for_lesson_urls(j)
            if links:
                print(f" -> Found {len(links)} candidate lesson links (showing up to 5):")
                for t,u in links[:5]:
                    print("   -", t, "->", u)
                discovered.extend(links)
            else:
                print(" -> No lesson links found in this response.")
        else:
            print("response preview:", preview[:400])

    # dedupe
    seen = set()
    deduped: List[Tuple[str,str]] = []
    for t,u in discovered:
        if u not in seen:
            seen.add(u)
            deduped.append((t,u))

    print("\nTotal candidate links discovered:", len(deduped))

    # write minimal page
    html = ["<!doctype html><html><head><meta charset='utf-8'><title>Oak lesson links</title></head><body>",
            "<h1>Oak lesson links (debug)</h1><ul>"]
    if deduped:
        for t,u in deduped:
            safe = (t or u).replace("<","&lt;").replace(">","&gt;")
            html.append(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
    else:
        html.append("<li>No links discovered in debug run. See full logs above for which endpoints returned data.</li>")
    html.append("</ul></body></html>")
    OUT_PATH.write_text("\n".join(html), encoding="utf-8")
    print("Wrote", OUT_PATH, "with", len(deduped), "links.")

if __name__ == "__main__":
    main()
