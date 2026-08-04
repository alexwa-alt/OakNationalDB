#!/usr/bin/env python3
"""
Inspector: fetch /sequences/{slug}/units and pretty-print the first unit JSON
so we can see the exact field names for lessons/ids/slugs.

After this run, paste the entire Actions step log for the "Run generator to build site/index.html"
so I can use the real structure to finish the extractor.
"""
from __future__ import annotations
import os
import json
import requests
from pathlib import Path
from typing import Any, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-inspector/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

BASE = "https://open-api.thenational.academy/api/v0"
PROGRAMME_SLUG = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")
OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
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

def main():
    seq_url = f"{BASE}/sequences/{PROGRAMME_SLUG}/units"
    print("Fetching:", seq_url)
    status, j, text = get_json(seq_url)
    print("Status:", status)
    if j is None:
        print("No JSON returned. Response preview (first 2000 chars):")
        print(text[:2000])
    else:
        print("Top-level JSON type:", type(j).__name__)
        if isinstance(j, list):
            print("List length:", len(j))
            if len(j) > 0 and isinstance(j[0], dict):
                print("First top-level item keys:", list(j[0].keys())[:50])
            first_unit = None
            for item in j:
                if isinstance(item, dict):
                    if "units" in item and isinstance(item["units"], list) and item["units"]:
                        first_unit = item["units"][0]
                        print("\nFound 'units' inside a year entry. Pretty-printing the first unit object:")
                        print(json.dumps(first_unit, indent=2, ensure_ascii=False)[:16000])
                        break
            if first_unit is None:
                for element in j:
                    if isinstance(element, dict) and ("slug" in element or "unitSlug" in element or "id" in element):
                        first_unit = element
                        print("\nList appears to contain units directly. Pretty-printing first element:")
                        print(json.dumps(first_unit, indent=2, ensure_ascii=False)[:16000])
                        break
            if first_unit is None:
                print("\nCouldn't automatically locate a unit object. Showing the first top-level item (truncated):")
                print(json.dumps(j[0], indent=2, ensure_ascii=False)[:16000])
        elif isinstance(j, dict):
            print("Top-level keys:", list(j.keys())[:50])
            for k in ("units","data","results"):
                if k in j:
                    print(f"\nKey '{k}' exists and type:", type(j[k]).__name__)
                    if isinstance(j[k], list) and j[k]:
                        print(f"First element of '{k}':")
                        print(json.dumps(j[k][0], indent=2, ensure_ascii=False)[:16000])
                        break
            else:
                print("\nTop-level dict preview (truncated):")
                print(json.dumps(j, indent=2, ensure_ascii=False)[:16000])

    OUT_PATH.write_text("<html><body><h1>Inspector run — check Actions logs for JSON output</h1></body></html>", encoding="utf-8")
    print("\nWrote placeholder", OUT_PATH)

if __name__ == "__main__":
    main()
