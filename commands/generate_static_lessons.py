#!/usr/bin/env python3
"""
Probe endpoints for a single unit to find where lessons are exposed.

Replace the generator temporarily with this file, run the workflow, and paste
the full step log here. The script:
- fetches /sequences/{slug}/units and takes the first unitSlug found
- tries a list of plausible endpoints (many variants) for that unit or its threads
- prints status, top-level JSON info or preview for each request
- writes a small placeholder site/index.html so the workflow still produces an artifact
"""
from __future__ import annotations
import os, json, requests
from pathlib import Path
from typing import Any, List, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-probe/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site"); OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"

BASES = [
    "https://open-api.thenational.academy/api/v0",
    "https://open-api.thenational.academy",
    "https://api.thenational.academy",
    "https://www.thenational.academy",
]

SEQUENCE = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")
TIMEOUT = 15

def get_json(url: str) -> Tuple[int, Any, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        status = r.status_code
        text_preview = (r.text[:1200] + "...") if len(r.text) > 1200 else r.text
        try:
            j = r.json()
        except Exception:
            j = None
        return status, j, text_preview
    except Exception as e:
        return 0, None, f"EXCEPTION: {e}"

def try_paths(unit_slug: str, thread_slugs: List[str]) -> None:
    # Candidate path templates to try (unit_slug or thread_slug inserted)
    candidate_templates = [
        "/units/{u}",
        "/units/{u}/lessons",
        "/units/{u}?include=lessons",
        "/sequences/{s}/units/{u}",
        "/sequences/{s}/units/{u}/lessons",
        "/lessons?unitSlug={u}",
        "/lessons?unit={u}",
        "/lessons?unit_slug={u}",
        "/lessons?sequence={s}",
        "/lessons?sequenceSlug={s}",
        "/threads/{t}",
        "/threads/{t}/lessons",
        "/threads/{t}?include=lessons",
        "/programmes/{s}/units/{u}/lessons",
        "/api/v0/units/{u}/lessons",
    ]

    tried = 0
    for base in BASES:
        for tpl in candidate_templates:
            path = tpl.replace("{u}", unit_slug).replace("{s}", SEQUENCE)
            # for thread templates, skip if not a thread template
            if "{t}" in tpl:
                for t in thread_slugs:
                    p = tpl.replace("{t}", t).replace("{u}", unit_slug).replace("{s}", SEQUENCE)
                    url = base.rstrip("/") + "/" + p.lstrip("/")
                    tried += 1
                    print(f"\nTRY -> {url}")
                    status, j, preview = get_json(url)
                    print("  status:", status)
                    if j is not None:
                        print("  json type:", type(j).__name__)
                        if isinstance(j, dict):
                            print("  keys:", list(j.keys())[:12])
                        elif isinstance(j, list):
                            print("  list length:", len(j))
                            if len(j) > 0 and isinstance(j[0], dict):
                                print("  first item keys:", list(j[0].keys())[:12])
                        # show a short preview
                        print("  preview (truncated):")
                        print(preview[:800])
                    else:
                        print("  preview (text):")
                        print(preview[:400])
            else:
                url = base.rstrip("/") + "/" + path.lstrip("/")
                tried += 1
                print(f"\nTRY -> {url}")
                status, j, preview = get_json(url)
                print("  status:", status)
                if j is not None:
                    print("  json type:", type(j).__name__)
                    if isinstance(j, dict):
                        print("  keys:", list(j.keys())[:12])
                    elif isinstance(j, list):
                        print("  list length:", len(j))
                        if len(j) > 0 and isinstance(j[0], dict):
                            print("  first item keys:", list(j[0].keys())[:12])
                    print("  preview (truncated):")
                    print(preview[:800])
                else:
                    print("  preview (text):")
                    print(preview[:400])
    print(f"\nTried {tried} endpoints across {len(BASES)} base hosts.")

def main():
    seq_units_url = f"https://open-api.thenational.academy/api/v0/sequences/{SEQUENCE}/units"
    print("Fetching sequence units:", seq_units_url)
    status, j, preview = get_json(seq_units_url)
    print("Status:", status)
    if j is None:
        print("No JSON for sequence units. Preview:")
        print(preview[:1200])
        OUT_PATH.write_text("<html><body><h1>No JSON from sequence units</h1></body></html>", encoding="utf-8")
        return

    # Find the first unitSlug and any threadSlugs we can use
    first_unit_slug = None
    thread_slugs: List[str] = []
    if isinstance(j, list):
        for entry in j:
            if isinstance(entry, dict) and "units" in entry and isinstance(entry["units"], list) and entry["units"]:
                first_unit = entry["units"][0]
                first_unit_slug = first_unit.get("unitSlug") or first_unit.get("slug") or first_unit.get("unit_slug") or first_unit.get("id")
                # collect any thread slugs
                threads = first_unit.get("threads") or []
                for th in threads:
                    if isinstance(th, dict):
                        ts = th.get("threadSlug") or th.get("slug") or th.get("thread_slug")
                        if ts:
                            thread_slugs.append(ts)
                break
    if not first_unit_slug:
        print("Could not find a unit slug in the sequence units response. Dumping top-level preview:")
        print(preview[:1600])
        OUT_PATH.write_text("<html><body><h1>No unit slug found</h1></body></html>", encoding="utf-8")
        return

    print("Probing endpoints for unit slug:", first_unit_slug)
    if thread_slugs:
        print("Found thread slugs:", thread_slugs[:5])
    try_paths(first_unit_slug, thread_slugs)

    OUT_PATH.write_text("<html><body><h1>Probe run complete — check Actions logs for output</h1></body></html>", encoding="utf-8")
    print("\nWrote placeholder", OUT_PATH)

if __name__ == "__main__":
    main()
