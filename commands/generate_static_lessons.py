#!/usr/bin/env python3
"""
Programme page inspector.

Fetches the teacher programme overview page and prints:
- HTTP status
- number of raw '/lessons/' href occurrences and small context snippets
- contents (keys / sample) of any <script id="__NEXT_DATA__"> JSON
- any <script type="application/json"> blocks that mention 'lesson' or 'unit'

Run in Actions and paste the entire step logs here.
"""
from __future__ import annotations
import os
import re
import json
import requests
from pathlib import Path
from typing import Any

TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-ks3")
URL = f"https://www.thenational.academy/teachers/programmes/{TEACHER_SEQ}"

OUT_DIR = Path("site"); OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "index.html").write_text("<html><body><h1>Inspector run — check logs</h1></body></html>", encoding="utf-8")

HEADERS = {"User-Agent": "oak-inspector/1.0", "Accept": "text/html,application/xhtml+xml"}

def fetch(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        return r.status_code, r.text
    except Exception as e:
        return 0, f"EXCEPTION: {e}"

def show_contexts(html: str, pattern: str, radius: int = 120, max_examples: int = 10):
    matches = list(re.finditer(pattern, html))
    print(f"Matches for pattern {pattern!r}: {len(matches)}")
    for i, m in enumerate(matches[:max_examples], start=1):
        start = max(0, m.start() - radius)
        end = min(len(html), m.end() + radius)
        snippet = html[start:end].replace("\n", " ")
        # shorten snippet if too long
        if len(snippet) > 400:
            snippet = snippet[:190] + " ... " + snippet[-190:]
        print(f"\n  Match {i} (pos {m.start()}): ...{snippet}...")

def extract_next_data(html: str):
    # Look for <script id="__NEXT_DATA__" type="application/json">...</script>
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, flags=re.S|re.I)
    if not m:
        print("No __NEXT_DATA__ <script> tag found.")
        return
    raw = m.group(1).strip()
    try:
        obj = json.loads(raw)
    except Exception as e:
        print("Failed to parse __NEXT_DATA__ JSON:", e)
        # print truncated raw
        print("Raw __NEXT_DATA__ (first 4000 chars):")
        print(raw[:4000])
        return
    # Print top-level keys and try to locate 'lessons' or 'units' inside
    print("__NEXT_DATA__ parsed JSON top-level keys:", list(obj.keys()))
    # Recursively search for 'lessons'/'units' keys and show small previews
    def search_and_print(o, path="root", depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if k and ("lesson" in k.lower() or "unit" in k.lower() or "lessonSlug" in k or "unitSlug" in k):
                    print(f"\nFound key '{k}' at path {path}.{k} type={type(v).__name__}")
                    try:
                        s = json.dumps(v, indent=2, ensure_ascii=False)[:4000]
                        print(s)
                    except Exception:
                        print(repr(v)[:1000])
                # recurse
                if isinstance(v, (dict, list)):
                    search_and_print(v, path=f"{path}.{k}", depth=depth+1)
        elif isinstance(o, list):
            for idx, it in enumerate(o[:20]):
                search_and_print(it, path=f"{path}[{idx}]", depth=depth+1)
    search_and_print(obj)
    print("\nFinished scanning __NEXT_DATA__.")

def extract_json_script_blocks(html: str):
    # Find <script type="application/json"> blocks that mention lessons/units
    scripts = re.findall(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', html, flags=re.S|re.I)
    print(f"Found {len(scripts)} application/json <script> blocks.")
    for i, s in enumerate(scripts[:6], start=1):
        if "lesson" in s.lower() or "unit" in s.lower():
            print(f"\nJSON script block #{i} contains 'lesson' or 'unit' (truncated):")
            try:
                j = json.loads(s)
                print("  top-level type:", type(j).__name__)
                if isinstance(j, dict):
                    print("  keys:", list(j.keys())[:30])
                else:
                    print("  length:", len(j) if isinstance(j, (list, dict)) else 'n/a')
                print("  preview:", json.dumps(j, indent=2, ensure_ascii=False)[:2000])
            except Exception as e:
                print("  failed to parse JSON block:", e)
                print("  raw (first 2000 chars):")
                print(s[:2000])

def main():
    print("Fetching programme page:", URL)
    status, html = fetch(URL)
    print("Status:", status)
    if status != 200 or not html:
        print("Failed to fetch programme page or empty body. Output (truncated):")
        print(html[:2000])
        return

    # show occurrences of /lessons/ in raw HTML
    show_contexts(html, r"/lessons/")

    # show occurrences of 'teachers/programmes/{TEACHER_SEQ}' (sanity)
    show_contexts(html, re.escape(f"/teachers/programmes/{TEACHER_SEQ}"))

    # try to extract __NEXT_DATA__ JSON
    extract_next_data(html)

    # try other JSON script blocks
    extract_json_script_blocks(html)

    # print first 8000 chars of page head to inspect meta tags
    head_match = re.search(r"<head.*?>(.*?)</head>", html, flags=re.S|re.I)
    if head_match:
        head = head_match.group(1).strip().replace("\n"," ")
        print("\nHead preview (truncated 4000 chars):\n", head[:4000])
    else:
        print("\nNo <head> section found in page or failed to parse.")

if __name__ == '__main__':
    main()
