#!/usr/bin/env python3
import os
import sys
import requests
import json

LESSON = os.environ.get("LESSON_SLUG")
API_KEY = os.environ.get("OAK_API_KEY")
if not LESSON:
    print("Missing LESSON_SLUG env var", file=sys.stderr)
    sys.exit(2)
if not API_KEY:
    print("Missing OAK_API_KEY in environment", file=sys.stderr)
    sys.exit(2)

url = f"https://open-api.thenational.academy/api/v0/lessons/{LESSON}/summary"
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
try:
    r = requests.get(url, headers=headers, timeout=20)
    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:1000])
        sys.exit(1)
    j = r.json()
    # Pretty-print top-level keys and look for objectives
    print("Top-level keys:", list(j.keys()))
    # Look for likely objective fields
    for candidate in ("learningObjectives", "objectives", "learningObjectivesText", "objectivesText", "objectives_list"):
        if candidate in j:
            print(f"Found field {candidate}:")
            print(json.dumps(j[candidate], indent=2)[:8000])
    # Fallback: print common nested 'data' keys
    if "data" in j and isinstance(j["data"], dict):
        print("Data keys:", list(j["data"].keys()))
        for candidate in ("learningObjectives", "objectives"):
            if candidate in j["data"]:
                print(f"Found data.{candidate}:")
                print(json.dumps(j["data"][candidate], indent=2)[:8000])
    # If nothing found, print a short excerpt
    if not any(k in j for k in ("learningObjectives", "objectives")) and not ("data" in j and any(k in j["data"] for k in ("learningObjectives", "objectives"))):
        print("No explicit objectives field found; printing sample of JSON:")
        print(json.dumps(j, indent=2)[:8000])
except Exception as e:
    print(f"Error fetching summary: {e}", file=sys.stderr)
    sys.exit(1)
