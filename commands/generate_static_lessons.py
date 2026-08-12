#!/usr/bin/env python3
"""
Generator that:
- fetches units from the Open API sequence (API sequence slug),
- fetches the corresponding teacher unit pages (teacher sequence slug),
- scrapes lesson links from those pages,
- validates/fetches lesson metadata from Open API /lessons/{lessonSlug}/summary,
- writes site/index.html with the discovered lesson links.

Behaviour: group links by teacher unit title (one section per Oak unit).
Lessons are deduplicated globally (first seen wins) so a lesson appearing
in multiple teacher units will only be shown under the first unit it was
encountered during the scan.

Fixes included:
- Use API-fetched lesson title where available so the link text is the
  canonical lesson title (avoids including the lesson objective in the
  anchor text).
- Use natural sort for lesson ordering so "10" sorts after "9" not before "1".
- If API title is unavailable, clean the scraped title removing 'I can' objectives
  and fixing missing punctuation for numeric prefixes (e.g., '1What' -> '1. What').
"""

from __future__ import annotations
import os
import sys
import time
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

API_KEY = os.environ.get("OAK_API_KEY")
API_SEQ = os.environ.get("OAK_API_SEQUENCE", "science-secondary-aqa")
TEACHER_SEQ = os.environ.get("OAK_TEACHER_SEQUENCE", "science-secondary-ks3")

API_BASE = "https://open-api.thenational.academy/api/v0"
TEACHER_BASE = "https://www.thenational.academy/teachers/programmes"

HEADERS = {"Accept": "application/json", "User-Agent": "oak-scrapegen/1.0"}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"

OUT_DIR = Path("site")
OUT_DIR.mkdir(exist_ok=True)
OUT_PATH = OUT_DIR / "index.html"
REQUEST_TIMEOUT = 15


def get_json(url: str, max_attempts: int = 5, backoff_factor: float = 0.5) -> Tuple[int, Any]:
    """GET JSON with retries and exponential backoff on 429/5xx and transient errors."""
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            # success
            if r.status_code == 200:
                return 200, r.json() if r.content else None
            # retryable responses
            if r.status_code == 429 or (500 <= r.status_code < 600):
                wait = backoff_factor * (2 ** (attempt - 1))
                print(f"JSON GET {url} returned {r.status_code}; retrying after {wait:.1f}s (attempt {attempt}/{max_attempts})", file=sys.stderr)
                time.sleep(wait)
                continue
            # non-retryable status - return what we have
            try:
                return r.status_code, r.json() if r.content else None
            except Exception:
                return r.status_code, None
        except Exception as e:
            wait = backoff_factor * (2 ** (attempt - 1))
            print(f"JSON GET error for {url}: {e}; retrying after {wait:.1f}s (attempt {attempt}/{max_attempts})", file=sys.stderr)
            time.sleep(wait)
            continue
    print(f"JSON GET failed after {max_attempts} attempts for {url}", file=sys.stderr)
    return 0, None


def get_html(url: str) -> Tuple[int, str]:
    try:
        r = requests.get(url, headers={"User-Agent": "oak-scrapegen/1.0"}, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.text
    except Exception as e:
        print(f"HTML GET error for {url}: {e}", file=sys.stderr)
        return 0, ""


def fetch_units(api_sequence_slug: str) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sequences/{api_sequence_slug}/units"
    print(f"Fetching units from API: {url}")
    status, j = get_json(url)
    if status != 200 or not isinstance(j, list):
        print(f"Failed to fetch units: status={status} json_type={type(j)}", file=sys.stderr)
        return []
    return j


def get_html_from_teacher_unit_url(url: str) -> str:
    status, html = get_html(url)
    if status != 200:
        print(f"HTMl fetch returned {status} for {url}", file=sys.stderr)
        return ""
    return html


def fetch_units_from_teacher(api_sequence_slug: str, teacher_sequence_slug: str) -> List[Tuple[str, str]]:
    """Return a list of (title, url) for teacher unit pages discovered for the sequence."""
    units_url = f"{TEACHER_BASE}/{teacher_sequence_slug}/units"
    print(f"Fetching teacher units listing: {units_url}")
    status, html = get_html(units_url)
    if status != 200:
        print(f"Failed to get teacher units page: {status}", file=sys.stderr)
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/units/" in href:
            title = (a.get_text() or href).strip()
            full = urljoin(TEACHER_BASE, href)
            links.append((title, full))
    return links


def fetch_units_from_api(api_sequence_slug: str) -> List[Tuple[str, str]]:
    # Fallback: build unit links from API unit slugs
    units = fetch_units(api_sequence_slug)
    out: List[Tuple[str, str]] = []
    for u in units:
        slug = u.get("slug")
        title = u.get("title") or slug
        if slug:
            out.append((title, f"{TEACHER_BASE}/{TEACHER_SEQ}/units/{slug}"))
    return out


def get_teacher_unit_lessons(teacher_unit_url: str) -> List[Tuple[str, str]]:
    """Scrape a teacher unit page for lesson links. Returns list of (raw_title, href)"""
    html = get_html_from_teacher_unit_url(teacher_unit_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # lessons path pattern in teacher pages
        if "/lessons/" in href:
            raw_title = (a.get_text() or href).strip()
            full = urljoin(TEACHER_BASE, href) if not href.startswith("http") else href
            links.append((raw_title, full))
    # de-duplicate by href while preserving the last title seen for that page on the unit
    seen = {}
    for t, u in links:
        seen[u] = t
    result = [(seen[u], u) for u in seen]
    return result


def fetch_lesson_summary(lesson_slug: str) -> Tuple[int, Any]:
    url = f"{API_BASE}/lessons/{lesson_slug}/summary"
    return get_json(url)


def extract_lesson_slug_from_url(url: str) -> str:
    """Extract the lesson slug from a teacher or public lesson URL."""
    try:
        p = urlparse(url)
        path = p.path  # e.g. /lessons/what-forces-do
        if "/lessons/" in path:
            slug = path.split("/lessons/", 1)[1].strip("/ ")
            # slug might include further segments; take first
            slug = slug.split("/")[0]
            return slug
    except Exception:
        pass
    return ""


def clean_title(raw: str) -> str:
    """Clean raw link text as a fallback when API title isn't available.
    - remove objectives that begin with phrases like 'I can'
    - ensure a space after a leading digit (e.g. '1What' -> '1. What')
    - collapse whitespace
    """
    if not raw:
        return ""
    s = raw.replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    # remove common objective marker 'I can' and anything after it
    m = re.search(r"(.+?)(?:\bI can\b).*", s, flags=re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    # put a dot+space after leading number if missing
    s = re.sub(r"^(\d+)([A-Za-z])", r"\1. \2", s)
    # if number is followed immediately by word without punctuation, add '. '
    s = re.sub(r"^(\d+)(\s*)([A-Za-z])", lambda mo: f"{int(mo.group(1))}. {mo.group(3)}" if mo.group(2)=="" else mo.group(0), s)
    return s.strip()


def lesson_sort_key(title: str) -> Tuple[int, str]:
    """Natural sort key: leading number (if present) then remainder lowercased."""
    if not title:
        return (9999, "")
    m = re.match(r"^\s*(\d+)[\.)\s-]*\s*(.*)$", title)
    if m:
        num = int(m.group(1))
        rest = m.group(2) or ""
        return (num, rest.lower())
    # try to find leading digits stuck to words e.g. '1What'
    m2 = re.match(r"^\s*(\d+)([A-Za-z].*)$", title)
    if m2:
        num = int(m2.group(1))
        rest = m2.group(2)
        return (num, rest.lower())
    return (9999, title.lower())


def main():
    print("Starting generator. API key present:", bool(API_KEY))
    # discover teacher unit pages using teacher sequence listing; fallback to api sequence
    teacher_units = fetch_units_from_teacher(API_SEQ, TEACHER_SEQ)
    if not teacher_units:
        teacher_units = fetch_units_from_api(API_SEQ)

    print(f"Found {len(teacher_units)} teacher units to scan")

    # Map of unit title -> list of (title, href)
    from collections import OrderedDict
    unit_map: "OrderedDict[str, List[Tuple[str, str]]]" = OrderedDict()
    global_seen = set()  # hrefs seen so far; used for global dedupe (first seen wins)

    for title, unit_url in teacher_units:
        print(f"Scanning unit: {title} -> {unit_url}")
        try:
            lessons = get_teacher_unit_lessons(unit_url)
            print(f"  found {len(lessons)} lesson links")
            unit_list: List[Tuple[str, str]] = []
            for raw_title, href in lessons:
                if href in global_seen:
                    # skip duplicate lessons already added under an earlier unit
                    continue
                # try to fetch canonical title from API using slug
                slug = extract_lesson_slug_from_url(href)
                canonical_title = None
                if slug:
                    status, data = fetch_lesson_summary(slug)
                    if status == 200 and isinstance(data, dict):
                        # try common keys
                        for key in ("title", "lessonTitle", "name", "displayName"):
                            if key in data and isinstance(data[key], str) and data[key].strip():
                                canonical_title = data[key].strip()
                                break
                        # sometimes nested
                        if not canonical_title and "data" in data and isinstance(data["data"], dict):
                            for key in ("title", "name"): 
                                if key in data["data"] and isinstance(data["data"][key], str):
                                    canonical_title = data["data"][key].strip()
                                    break
                    # small throttle for API calls
                    time.sleep(0.1)
                if not canonical_title:
                    canonical_title = clean_title(raw_title)
                global_seen.add(href)
                unit_list.append((canonical_title, href))
            # sort unit's lessons by natural numeric order
            unit_list.sort(key=lambda x: lesson_sort_key(x[0] or ""))
            unit_map[title] = unit_list
            # small throttle between unit pages
            time.sleep(0.25)
        except Exception as e:
            print(f"Error scanning {unit_url}: {e}", file=sys.stderr)

    total_links = sum(len(v) for v in unit_map.values())

    # build html
    html_lines: List[str] = []
    html_lines.append("<!doctype html>")
    html_lines.append("<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Oak lesson links</title>")
    html_lines.append("<style>body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial;padding:1rem}a{color:#0366d6}h2{margin-top:1.5rem}nav{margin-bottom:1rem}</style>")
    html_lines.append("</head><body>")
    # simple navigation so visitors can reach the new static page
    html_lines.append("<nav><a href=\"index.html\">Home</a> | <a href=\"new-page.html\">New page</a></nav>")
    html_lines.append(f"<h1>Oak lesson links — sequence: {API_SEQ}</h1>")
    html_lines.append(f"<p>Total links discovered: {total_links}</p>")

    if total_links == 0:
        html_lines.append("<p>No links discovered. See Actions logs for details.</p>")
    else:
        # render each unit as its own section
        for idx, (unit_title, lessons) in enumerate(unit_map.items(), start=1):
            # skip empty units
            if not lessons:
                continue
            safe_unit = (unit_title or f"Unit {idx}").replace("<", "&lt;").replace(">", "&gt;")
            html_lines.append(f"<h2>{safe_unit}</h2>")
            html_lines.append("<ul>")
            for title, href in lessons:
                safe = (title or href).replace("<", "&lt;").replace(">", "&gt;")
                html_lines.append(f'<li><a href="{href}" target="_blank" rel="noopener noreferrer">{safe}</a></li>')
            html_lines.append("</ul>")

    html_lines.append("</body></html>")

    OUT_PATH.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {total_links} links across {len([u for u in unit_map.values() if u])} unit(s)")

    # --- Build a KS3 Science unit -> aggregated keyLearningPoints map and render an interactive page ---
    import json

    ks3_units: dict[str, list] = {}
    CACHE_FILE = OUT_DIR / "ks3_units.json"
    refresh = os.environ.get("REFRESH_KS3_CACHE", "").lower() in ("1", "true", "yes")

    if not refresh and CACHE_FILE.exists():
        try:
            ks3_units = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            print(f"Loaded ks3_units cache from {CACHE_FILE} with {len(ks3_units)} unit(s)")
        except Exception as e:
            print(f"Failed to load cache {CACHE_FILE}: {e}; regenerating", file=sys.stderr)
            ks3_units = {}
            refresh = True

    if refresh or not ks3_units:
        for unit_title, lessons in unit_map.items():
            aggregated = []
            seen = set()
            for title, href in lessons:
                slug = extract_lesson_slug_from_url(href)
                if not slug:
                    continue
                status, data = fetch_lesson_summary(slug)
                if status != 200 or not isinstance(data, dict):
                    time.sleep(0.25)
                    continue
                ks = (data.get("keyStageSlug") or data.get("keyStageTitle") or (data.get("data") or {}).get("keyStageSlug") or "").lower()
                subj = (data.get("subjectSlug") or data.get("subjectTitle") or (data.get("data") or {}).get("subjectSlug") or "").lower()
                if ("ks3" in ks) or ("key stage 3" in ks) or ("science" in subj):
                    key_points = data.get("keyLearningPoints") or (data.get("data") or {}).get("keyLearningPoints") or []
                    # normalize to list of strings and extract 'keyLearningPoint' when items are dicts
                    if isinstance(key_points, str):
                        items = [key_points]
                    elif isinstance(key_points, list):
                        processed = []
                        for x in key_points:
                            if isinstance(x, dict):
                                # prefer common keys that contain the text
                                for k in ("keyLearningPoint", "text", "learningPoint", "point"):
                                    if k in x and isinstance(x[k], str) and x[k].strip():
                                        processed.append(x[k].strip())
                                        break
                                else:
                                    # fallback: pick the first string value in the dict
                                    vals = [v for v in x.values() if isinstance(v, str) and v.strip()]
                                    processed.append(vals[0].strip() if vals else str(x))
                            else:
                                processed.append(str(x))
                        items = processed
                    else:
                        items = [str(key_points)]
                    for p in items:
                        txt = (p or "").strip()
                        if txt and txt not in seen:
                            seen.add(txt)
                            aggregated.append(txt)
                # throttle
                time.sleep(0.25)
            if aggregated:
                ks3_units[unit_title or ""] = aggregated

        try:
            CACHE_FILE.write_text(json.dumps(ks3_units, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"Wrote KS3 units cache to {CACHE_FILE} with {len(ks3_units)} unit(s)")
        except Exception as e:
            print(f"Failed to write cache {CACHE_FILE}: {e}", file=sys.stderr)

    else:
        print("Using cached ks3_units; set REFRESH_KS3_CACHE=1 to regenerate")

    # write interactive page with embedded data
    sample_out = OUT_DIR / "new-page.html"
    page = [
        "<!doctype html>",
        "<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta name='theme-color' content='#123047'><title>KS3 Science quiz builder</title>",
        "<style>:root{--navy:#123047;--blue:#176b87;--teal:#127475;--mint:#dff5ef;--ink:#182631;--muted:#5c6b75;--line:#d8e2e7;--paper:#fff;--canvas:#f3f7f8;--focus:#f4a261}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;line-height:1.5}.site-shell{width:min(100% - 2rem,72rem);margin:auto}.site-header{background:linear-gradient(125deg,var(--navy),var(--blue));color:#fff;box-shadow:0 2px 12px #12304733}.site-nav{display:flex;gap:1rem;padding:.85rem 0}.site-nav a{color:#fff;font-weight:700;text-decoration:none}.site-nav a:hover{text-decoration:underline}.hero{padding:2.6rem 0 1.8rem}.eyebrow{margin:0 0 .35rem;color:var(--teal);font-size:.8rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.hero h1{margin:0;font-size:clamp(2rem,5vw,3.25rem);line-height:1.05;letter-spacing:-.04em}.hero p:last-child{max-width:42rem;margin:1rem 0 0;color:var(--muted);font-size:1.08rem}.control-card,.quiz-section{margin:1.25rem 0;padding:1.25rem;border:1px solid var(--line);border-radius:1rem;background:var(--paper);box-shadow:0 8px 24px #1230470d}.control-row{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end}.control-row label{display:block;font-weight:750}.control-row select{min-width:min(100%,28rem);margin-top:.3rem;padding:.65rem .75rem;border:1px solid #a9bcc5;border-radius:.55rem;background:#fff;color:var(--ink);font:inherit}details{margin-top:1.1rem;border-top:1px solid var(--line);padding-top:1rem}summary{cursor:pointer;color:var(--blue);font-weight:750}summary:hover{color:var(--teal)}#points{display:grid;gap:.55rem;margin-top:.8rem;color:var(--muted)}#points div{padding:.65rem .75rem;border-left:3px solid var(--mint);background:#f8fbfb}button{border:0;border-radius:.55rem;padding:.65rem .9rem;background:#e8eef1;color:var(--ink);font:inherit;font-weight:750;cursor:pointer;transition:transform .15s ease,background .15s ease,box-shadow .15s ease}button:hover:not(:disabled){transform:translateY(-1px);background:#d8e5e9;box-shadow:0 3px 8px #12304722}button:disabled{cursor:not-allowed;opacity:.55}button:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:3px}#quiz-btn{margin-top:1rem;background:var(--teal);color:#fff}#quiz-btn:hover:not(:disabled){background:#0d6163}.section-heading{display:flex;align-items:center;gap:.65rem;margin:0;font-size:1.35rem}.section-heading::before{width:.65rem;height:.65rem;border-radius:50%;background:var(--teal);content:''}.quiz-status{display:inline-block;margin:.8rem 0 0;padding:.4rem .65rem;border-radius:999px;background:var(--mint);color:#075e5d;font-size:.92rem;font-weight:650}.quiz-status:empty{display:none}#quiz{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;margin-top:1.25rem}#quiz h3,#quiz>.quiz-navigation{grid-column:1/-1;margin:0}.quiz-question{padding:1.1rem;border:1px solid var(--line);border-radius:.85rem;background:#fff;box-shadow:0 4px 12px #1230470a}.quiz-question>p:first-child{margin-top:0;font-weight:700}.quiz-options{margin:.8rem 0;padding-left:1.5rem}.quiz-options li{margin:.45rem 0;padding-left:.2rem}.correct-answer{margin:.85rem 0 0;padding:.6rem .7rem;border-radius:.45rem;background:#e7f7ee;color:#116329;font-weight:700}.quiz-navigation{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}.quiz-navigation button{margin:0}.icon-button{min-width:2.5rem;padding:.55rem .7rem;color:var(--blue);font-size:1.15rem}.print-button{background:var(--navy);color:#fff}.print-button:hover:not(:disabled){background:#0b2539}.answer-print-button{background:#d9f2d9;color:#116329}@media(max-width:44rem){.site-shell{width:min(100% - 1.25rem,72rem)}.hero{padding:2rem 0 1.15rem}.control-card,.quiz-section{padding:1rem;border-radius:.75rem}#quiz{grid-template-columns:1fr}.control-row select{width:100%;min-width:0}}</style>",
        "</head><body>",
        "<header class=\"site-header\"><div class=\"site-shell\"><nav class=\"site-nav\" aria-label=\"Primary navigation\"><a href=\"index.html\">Home</a><a href=\"new-page.html\" aria-current=\"page\">Quiz builder</a></nav></div></header>",
        "<main class=\"site-shell\">",
        "<section class=\"hero\"><p class=\"eyebrow\">KS3 Science</p><h1>Quiz builder</h1><p>Choose a unit, create a fresh set of questions from the available quiz bank, then print it for your class.</p></section>",
        "<section class=\"control-card\" aria-labelledby=\"unit-heading\"><h2 id=\"unit-heading\" class=\"section-heading\">Choose a unit</h2><div class=\"control-row\"><div><label for=\"unit-select\">Unit</label>",
        "<select id=\"unit-select\"><option value=\"\">-- choose a unit --</option></select></div></div>",
        "<details id=\"learning-points\">",
        "<summary>Show learning objectives</summary>",
        "<button id=\"copy-btn\" type=\"button\">Copy all</button>",
        "<div id=\"points\"></div>",
        "</details>",
        "</section>",
        "<section class=\"quiz-section\" aria-labelledby=\"quiz-heading\">",
        "<h2 id=\"quiz-heading\" class=\"section-heading\">Unit quiz</h2>",
        "<button id=\"quiz-btn\" type=\"button\" disabled>Generate quiz</button>",
        "<p id=\"quiz-status\" class=\"quiz-status\" role=\"status\"></p>",
        "<div id=\"quiz\" aria-live=\"polite\"></div>",
        "</section>",
        "</main>",
        "<script>",
        f"const UNITS = {json.dumps(ks3_units)};",
        "const sel = document.getElementById('unit-select');",
        "const copyBtn = document.getElementById('copy-btn');",
        "const pointsDiv = document.getElementById('points');",
        "const quizBtn = document.getElementById('quiz-btn');",
        "const quizStatus = document.getElementById('quiz-status');",
        "const quizDiv = document.getElementById('quiz');",
        "const QUIZ_MANIFEST_URL = 'quizzes/index.json';",
        "const QUESTION_COUNT = 20;",
        "let quizQuestions = [];",
        "let challengingQuestions = [];",
        "let selectedQuestionIndexes = [];",
        "let quizManifest = null;",
        "function escapeHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');}",
        "function prettifyLabel(s){ if(!s) return s; s = s.replace(/^(\\\\d+)([A-Za-z])/, '$1. $2'); s = s.replace(/([a-z0-9])([A-Z])/g, '$1 $2'); s = s.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2'); s = s.replace(/[-_]/g,' '); return s; }",
        "function populateUnits(){Object.keys(UNITS).forEach(u=>{const o=document.createElement('option');o.value=u;o.textContent=prettifyLabel(u);o.title=u;sel.appendChild(o)});}  ",
        "function resetQuiz(){quizDiv.innerHTML=''; quizQuestions=[]; challengingQuestions=[]; selectedQuestionIndexes=[]; quizBtn.disabled=true; quizStatus.textContent='';}",
        "function showPoints(unit){pointsDiv.innerHTML=''; resetQuiz(); if(!unit) return; const items=UNITS[unit]||[]; if(items.length===0){pointsDiv.textContent='No key learning points found.'; return;} const frag=document.createDocumentFragment(); items.forEach(p=>{const div=document.createElement('div');div.textContent=p;frag.appendChild(div)}); pointsDiv.appendChild(frag); copyBtn.textContent='Copy all'; quizBtn.disabled=false;}",
        "async function copyAll(){ const unit = sel.value; if(!unit){ alert('Please select a unit first'); return; } const items = UNITS[unit]||[]; const text = items.join(String.fromCharCode(10)); try{ await navigator.clipboard.writeText(text); copyBtn.textContent='Copied!'; setTimeout(()=>copyBtn.textContent='Copy all',2000); } catch(e){ try{ const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.left='-9999px'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); copyBtn.textContent='Copied!'; setTimeout(()=>copyBtn.textContent='Copy all',2000); } catch(err){ alert('Copy failed'); } } }",
        "async function loadQuizQuestions(){if(quizQuestions.length)return; if(!quizManifest){const manifestResponse=await fetch(QUIZ_MANIFEST_URL,{cache:'no-store'});if(!manifestResponse.ok)throw new Error('Quiz cache is not published yet.');quizManifest=await manifestResponse.json();if(!quizManifest.units||typeof quizManifest.units!=='object')throw new Error('Quiz cache manifest is invalid.');}const entry=quizManifest.units[sel.value];if(!entry||typeof entry.path!=='string'||!/^[a-z0-9-]+$/.test(entry.path))throw new Error('No cached quizzes are available for this unit yet.');const response=await fetch(`quizzes/${entry.path}/index.json`,{cache:'no-store'});if(!response.ok)throw new Error('Quiz cache is not published yet.');const data=await response.json();if(!Array.isArray(data.quizzes)||!data.quizzes.every(name=>typeof name==='string'))throw new Error('Quiz cache index is invalid.');const quizzes=await Promise.all(data.quizzes.map(async filename=>{const quizResponse=await fetch(`quizzes/${entry.path}/${filename}`,{cache:'no-store'});if(!quizResponse.ok)throw new Error('A cached quiz could not be loaded.');return quizResponse.json();}));quizQuestions=quizzes.flatMap(quiz=>Array.isArray(quiz.questions)?quiz.questions:[]);if(!quizQuestions.length)throw new Error('No cached questions are available.');quizQuestions.sort(()=>Math.random()-.5);}",
        "async function loadChallengingQuestions(){if(challengingQuestions.length)return; if(!quizManifest)await loadQuizQuestions();const entry=quizManifest.units[sel.value];if(!entry||typeof entry.challenging!=='string')throw new Error('Challenging questions are not available for this unit yet.');const response=await fetch(`quizzes/${entry.path}/${entry.challenging}`,{cache:'no-store'});if(!response.ok)throw new Error('Challenging questions are not available for this unit yet.');const data=await response.json();if(!Array.isArray(data.questions)||data.questions.length!==QUESTION_COUNT)throw new Error('The challenging question deck is invalid.');challengingQuestions=data.questions;}",
        "function deckFor(difficulty){return difficulty==='challenging'?challengingQuestions:quizQuestions;}",
        "async function setDifficulty(slot){const current=selectedQuestionIndexes[slot];if(current.difficulty==='challenging'){const used=new Set(selectedQuestionIndexes.filter(item=>item.difficulty==='standard').map(item=>item.index));const replacement=quizQuestions.findIndex((_,index)=>!used.has(index));if(replacement>=0){selectedQuestionIndexes[slot]={difficulty:'standard',index:replacement};renderQuestions();}return;}try{quizStatus.textContent='Loading challenging questions...';await loadChallengingQuestions();const used=new Set(selectedQuestionIndexes.filter(item=>item.difficulty==='challenging').map(item=>item.index));let replacement=challengingQuestions.findIndex((_,index)=>!used.has(index));if(replacement<0){const other=selectedQuestionIndexes.findIndex((item,index)=>index!==slot&&item.difficulty==='challenging');if(other<0)throw new Error('No challenging question is available.');replacement=selectedQuestionIndexes[other].index;const standardUsed=new Set(selectedQuestionIndexes.filter(item=>item.difficulty==='standard').map(item=>item.index));const standardReplacement=quizQuestions.findIndex((_,index)=>!standardUsed.has(index));selectedQuestionIndexes[other]={difficulty:'standard',index:standardReplacement};}selectedQuestionIndexes[slot]={difficulty:'challenging',index:replacement};quizStatus.textContent='';renderQuestions();}catch(error){quizStatus.textContent=error.message;}}",
        "function swapQuestion(slot,direction){const current=selectedQuestionIndexes[slot];const deck=deckFor(current.difficulty);for(let offset=1;offset<deck.length;offset++){const candidate=(current.index+direction*offset+deck.length)%deck.length;const other=selectedQuestionIndexes.findIndex((item,index)=>index!==slot&&item.difficulty===current.difficulty&&item.index===candidate);if(other>=0){[selectedQuestionIndexes[slot],selectedQuestionIndexes[other]]=[selectedQuestionIndexes[other],selectedQuestionIndexes[slot]];}else{selectedQuestionIndexes[slot]={difficulty:current.difficulty,index:candidate};}renderQuestions();return;}}",
        "function printQuiz(includeAnswers=false){const questions=selectedQuestionIndexes.map(item=>deckFor(item.difficulty)[item.index]); if(questions.length!==QUESTION_COUNT){alert('Generate a complete quiz before printing.');return;} const printWindow=window.open('','_blank');if(!printWindow){alert('Please allow pop-ups to print the quiz.');return;} const title=escapeHtml(prettifyLabel(sel.value));const documentTitle=`${title} ${includeAnswers?'answers':'quiz'}`;const pages=[questions.slice(0,10),questions.slice(10,20)].map((page,pageIndex)=>`<section class=\"quiz-page\"><h1>${documentTitle}</h1><div class=\"questions\">${page.map((item,index)=>`<article class=\"question\"><p><strong>${pageIndex*10+index+1}.</strong> ${escapeHtml(item.question)}</p><ol type=\"A\">${item.options.map((option,optionIndex)=>`<li class=\"${includeAnswers&&optionIndex===item.correctAnswerIndex?'correct-answer':''}\">${escapeHtml(option)}</li>`).join('')}</ol></article>`).join('')}</div></section>`).join('');printWindow.onload=()=>{printWindow.focus();printWindow.print();};printWindow.document.write(`<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>${documentTitle}</title><style>@page{size:A4;margin:12mm}body{font-family:Arial,sans-serif;color:#000;margin:0}.quiz-page{break-after:page;page-break-after:always}.quiz-page:last-child{break-after:auto;page-break-after:auto}h1{font-size:14pt;margin:0 0 5mm}.questions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5mm 8mm}.question{break-inside:avoid;page-break-inside:avoid;font-size:9pt;line-height:1.25}.question p{margin:0 0 2mm}.question ol{margin:0;padding-left:5mm}.question li{margin:0 0 1mm}.correct-answer{background:#d9f2d9;color:#116329;font-weight:700}</style></head><body>${pages}</body></html>`);printWindow.document.close();}",
        "function renderQuestions(){quizDiv.innerHTML=''; const heading=document.createElement('h3'); heading.textContent=`${prettifyLabel(sel.value)} questions`; quizDiv.appendChild(heading); selectedQuestionIndexes.forEach((selection,slot)=>{const item=deckFor(selection.difficulty)[selection.index]; const article=document.createElement('article'); article.className='quiz-question'; const question=document.createElement('p'); question.textContent=`${slot+1}. ${item.question}`; article.appendChild(question); const options=document.createElement('ol'); options.className='quiz-options'; options.type='A'; item.options.forEach(option=>{const li=document.createElement('li');li.textContent=option;options.appendChild(li);}); article.appendChild(options); const answer=document.createElement('p'); answer.className='correct-answer'; answer.hidden=true; answer.textContent=`Correct answer: ${String.fromCharCode(65+item.correctAnswerIndex)}. ${item.options[item.correctAnswerIndex]}`; article.appendChild(answer); const controls=document.createElement('div'); controls.className='quiz-navigation'; const previous=document.createElement('button'); previous.type='button';previous.className='icon-button';previous.textContent='←';previous.setAttribute('aria-label',`Replace question ${slot+1} with the previous question`);previous.addEventListener('click',()=>swapQuestion(slot,-1)); const difficulty=document.createElement('button');difficulty.type='button';difficulty.textContent=selection.difficulty==='challenging'?'Challenging':'Standard';difficulty.setAttribute('aria-label',`Toggle question ${slot+1} difficulty`);difficulty.addEventListener('click',()=>setDifficulty(slot)); const showAnswer=document.createElement('button'); showAnswer.type='button';showAnswer.textContent='Show answer';showAnswer.addEventListener('click',()=>{answer.hidden=false;showAnswer.remove();}); const next=document.createElement('button'); next.type='button';next.className='icon-button';next.textContent='→';next.setAttribute('aria-label',`Replace question ${slot+1} with the next question`);next.addEventListener('click',()=>swapQuestion(slot,1)); controls.append(previous,difficulty,showAnswer,next);article.appendChild(controls);quizDiv.appendChild(article);}); const printControls=document.createElement('div');printControls.className='quiz-navigation';const printButton=document.createElement('button');printButton.type='button';printButton.className='print-button';printButton.textContent='Print quiz';printButton.addEventListener('click',()=>printQuiz(false));const answerButton=document.createElement('button');answerButton.type='button';answerButton.className='answer-print-button';answerButton.textContent='Print answers';answerButton.addEventListener('click',()=>printQuiz(true));printControls.append(printButton,answerButton);quizDiv.appendChild(printControls);}",
        "async function generateQuiz(){if(!sel.value)return; quizBtn.disabled=true; quizStatus.textContent='Loading questions...'; try{await loadQuizQuestions(); quizQuestions.sort(()=>Math.random()-.5); selectedQuestionIndexes=Array.from({length:Math.min(QUESTION_COUNT,quizQuestions.length)},(_,index)=>({difficulty:'standard',index})); renderQuestions(); quizStatus.textContent='';}catch(error){quizStatus.textContent=error.message;}finally{if(sel.value)quizBtn.disabled=false;}}",
        "copyBtn.addEventListener('click', copyAll);",
        "quizBtn.addEventListener('click', generateQuiz);",
        "sel.addEventListener('change', e=>showPoints(e.target.value));",
        "populateUnits();",
        "</script>",

        "</body></html>",
    ]
    try:
        sample_out.write_text("\n".join(page), encoding='utf-8')
        print(f"Wrote {sample_out} with {len(ks3_units)} KS3 Science unit(s)")
    except Exception as e:
        print(f"Failed to write interactive page: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
