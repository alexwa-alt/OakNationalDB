#!/usr/bin/env python3
"""
Inspector: fetch /sequences/{slug} and pretty-print the sequence JSON (or a useful excerpt).

Run this in Actions, then paste the full step log here. That will show where lesson references live
so I can write the final extractor.

Writes a small placeholder site/index.html so the workflow still publishes.
"""
from __future__ import annotations
import os
import json
import requests
from pathlib import Path
from typing import Any, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-sequence-inspector/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

BASE = "https://open-api.thenational.academy/api/v0"
SEQUENCE = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")
OUT_DIR = Path("site"); OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"

def get_json(url: str) -> Tuple[int, Any, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        status = r.status_code
        text = r.text
        try:
            j = r.json()
        except Exception:
            j = None
        return status, j, text
    except Exception as e:
        return 0, None, f"EXCEPTION: {e}"

def pretty_print(obj: Any, label: str = "", limit: int = 16000) -> None:
    print(f"\n--- {label} (truncated to {limit} chars) ---")
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    print(s[:limit])

def main():
    seq_url = f"{BASE}/sequences/{SEQUENCE}"
    print("Fetching sequence:", seq_url)
    status, j, text = get_json(seq_url)
    print("Status:", status)
    if j is None:
        print("No JSON returned for sequence. Response preview (first 2000 chars):")
        print(text[:2000])
        OUT_PATH.write_text("<html><body><h1>No sequence JSON - inspector</h1></body></html>", encoding="utf-8")
        return

    # Top-level info
    print("Top-level JSON type:", type(j).__name__)
    if isinstance(j, dict):
        print("Top-level keys:", list(j.keys())[:50])
    elif isinstance(j, list):
        print("Top-level is a list; length:", len(j))
        # print keys of first item if dict
        if len(j) > 0 and isinstance(j[0], dict):
            print("First item keys:", list(j[0].keys())[:50])

    # Pretty-print useful subsections if present
    # 1) years
    if isinstance(j, dict) and "years" in j:
        pretty_print(j["years"], label="years")
        # if years is a list, try to print first year's units
        try:
            first_year = j["years"][0]
            if isinstance(first_year, dict) and "units" in first_year:
                pretty_print(first_year["units"], label="first_year.units")
        except Exception:
            pass

    # 2) units at top-level (some shapes may include units directly)
    if isinstance(j, dict) and "units" in j:
        pretty_print(j["units"], label="top-level.units")

    # 3) threads / lessons keys if present
    for k in ("lessons", "threads", "units", "years", "sequenceLessons", "sequence_lessons"):
        if isinstance(j, dict) and k in j:
            pretty_print(j[k], label=f"key: {k}")

    # 4) fallback: print a truncated dump of the entire sequence
    pretty_print(j, label="full-sequence-object", limit=12000)

    OUT_PATH.write_text("<html><body><h1>Sequence inspector run — check Actions logs for JSON output</h1></body></html>", encoding="utf-8")
    print("\nWrote placeholder", OUT_PATH)

if __name__ == "__main__":
    main()
