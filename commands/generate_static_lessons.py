#!/usr/bin/env python3
"""
Inspect sequence units response to show the exact JSON shape for debugging.

Replace the generator temporarily with this file, run the existing workflow,
and paste the workflow step output here so I can see the exact unit object.

Writes a tiny site/index.html as a placeholder.
"""
from __future__ import annotations
import os, json, requests
from pathlib import Path
from typing import Any, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
HEADERS = {"Accept": "application/json", "User-Agent": "oak-inspect/0.1"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

BASE = "https://open-api.thenational.academy/api/v0"
PROGRAMME_SLUG = os.environ.get("OAK_PROGRAMME_SLUG", "science-secondary-aqa")
OUT_DIR = Path("site"); OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"

def get_json(url: str) -> Tuple[int, Any, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        status = r.status_code
        text_preview = (r.text[:1000] + "...") if len(r.text) > 1000 else r.text
        try:
            j = r.json()
        except Exception:
            j = None
        return status, j, text_preview
    except Exception as e:
        return 0, None, f"EXCEPTION: {e}"

def main():
    seq_url = f"{BASE}/sequences/{PROGRAMME_SLUG}/units"
    print("Fetching:", seq_url)
    status, j, preview = get_json(seq_url)
    print("Status:", status)
    if j is None:
        print("No JSON returned. Preview (truncated):")
        print(preview[:800])
    else:
        # Print top-level type and keys
        print("Top-level type:", type(j).__name__)
        if isinstance(j, dict):
            print("Top-level keys:", list(j.keys())[:30])
        elif isinstance(j, list):
            print("List length:", len(j))
            if len(j) > 0:
                print("First item keys (if dict):", list(j[0].keys())[:30] if isinstance(j[0], dict) else "first item not a dict")
        # Pretty-print the first unit object if present
        first_unit = None
        if isinstance(j, list):
            # j is likely a list of year entries; try to find the first unit
            for item in j:
                if isinstance(item, dict) and item.get("units"):
                    units = item.get("units")
                    if isinstance(units, list) and len(units) > 0:
                        first_unit = units[0]
                        print("\nFound 'units' inside a year entry; printing the first unit object (pretty):")
                        print(json.dumps(first_unit, indent=2, ensure_ascii=False)[:8000])
                        break
            # fallback: if list appears to be units directly
            if first_unit is None and len(j) > 0 and isinstance(j[0], dict) and ("slug" in j[0] or "unitSlug" in j[0] or "id" in j[0]):
                first_unit = j[0]
                print("\nList appears to be units directly; printing first unit object:")
                print(json.dumps(first_unit, indent=2, ensure_ascii=False)[:8000])
        elif isinstance(j, dict):
            # try common keys
            for k in ("units","data","results"):
                if k in j and isinstance(j[k], list) and len(j[k]) > 0:
                    first_unit = j[k][0]
                    print(f"\nFound key '{k}' containing list; printing first element:")
                    print(json.dumps(first_unit, indent=2, ensure_ascii=False)[:8000])
                    break
        if first_unit is None:
            print("\nCouldn't locate a unit object to print automatically. Showing top-level preview:")
            print(json.dumps(j, indent=2, ensure_ascii=False)[:8000])

    # write a placeholder page so Pages stays happy
    OUT_PATH.write_text("<html><body><h1>Inspector run — check Actions logs for JSON output</h1></body></html>", encoding="utf-8")
    print("\nWrote placeholder", OUT_PATH)

if __name__ == "__main__":
    main()
