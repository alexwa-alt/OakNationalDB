#!/usr/bin/env python3
"""
Simple scraper that collects lesson links from an Oak sequence page
and writes a single static site/index.html with an unordered list of links.

Default sequence slug: science-secondary-aqa
Override with environment variable OAK_SEQUENCE_SLUG.
"""
from __future__ import annotations
import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path

BASE = "https://www.thenational.academy"
SLUG = os.environ.get("OAK_SEQUENCE_SLUG", "science-secondary-aqa")
OUT_DIR = Path("site")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "index.html"
TIMEOUT = 15

def fetch(url: str):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "oak-scraper/1.0"})
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"Fetch failed: {url} -> {e}", file=sys.stderr)
        return None

def absolute(href: str, base: str = BASE):
    return urljoin(base, href)

def collect_from_sequence(slug: str):
    seq_url = f"{BASE}/sequence/{slug}"
    html = fetch(seq_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    # All anchors that look like lesson links (contain '/lessons/')
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/lessons/" in href:
            links.add((a.get_text(strip=True) or href, absolute(href)))

    # Also follow unit links on the sequence page to collect more lessons
    unit_hrefs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/units/" in href or "/unit/" in href or "/sequence/" in href and "/units" in href:
            unit_hrefs.add(absolute(href))

    for uh in list(unit_hrefs)[:30]:  # limit to avoid accidental wide crawl
        uh_html = fetch(uh)
        if not uh_html:
            continue
        usoup = BeautifulSoup(uh_html, "html.parser")
        for a in usoup.find_all("a", href=True):
            href = a["href"]
            if "/lessons/" in href:
                links.add((a.get_text(strip=True) or href, absolute(href, uh)))

    # Normalize and return sorted list
    cleaned = []
    seen = set()
    for title, url in sorted(links, key=lambda x: x[1]):
        u = url.split("#")[0]
        if u not in seen:
            seen.add(u)
            cleaned.append((title or u, u))
    return cleaned

def build_page(links):
    html = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Oak lesson links</title>",
        "<style>body{font-family:system-ui, -apple-system, 'Segoe UI', Roboto, Arial; padding:1rem} a{color:#0366d6}</style>",
        "</head><body>",
        f"<h1>Oak lesson links — sequence: {SLUG}</h1>",
        "<p>Automatically generated. Links discovered:</p>",
        "<ul>"
    ]
    if links:
        for title, url in links:
            safe = (title or url).replace("<", "&lt;").replace(">", "&gt;")
            html.append(f'<li><a href="{url}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
    else:
        html.append("<li>No links discovered. Check Actions logs for diagnostics.</li>")
    html.extend(["</ul>", "</body></html>"])
    OUT_FILE.write_text("\n".join(html), encoding="utf-8")
    print(f"Wrote {OUT_FILE} with {len(links)} links.")

def main():
    print(f"Scraping sequence: {SLUG}")
    links = collect_from_sequence(SLUG)
    build_page(links)

if __name__ == "__main__":
    main()
