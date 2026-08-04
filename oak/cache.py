"""Simple file-based cache for raw API responses.

Cache layout:

data/cache/<key>.json

Where <key> is a safe filename derived from the request (slug or id).

The cache stores the raw JSON bytes and a .meta file with sha256, optional ETag
and a last_checked timestamp so imports can be resumed and conditional
requests performed.
"""
from __future__ import annotations
from pathlib import Path
import json
import hashlib
from typing import Optional, Tuple, Any
from datetime import datetime

from config import config


def _key_to_path(key: str) -> Path:
    safe = key.replace("/", "_")
    return config.cache_dir / f"{safe}.json"


def save_cache(key: str, content: Any, etag: Optional[str] = None) -> None:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    p = _key_to_path(key)
    raw = json.dumps(content, ensure_ascii=False, indent=None).encode("utf-8")
    p.write_bytes(raw)
    meta = {"sha256": hashlib.sha256(raw).hexdigest(), "last_checked": datetime.utcnow().isoformat()}
    if etag:
        meta["etag"] = etag
    p.with_suffix(".meta.json").write_text(json.dumps(meta))


def load_cache(key: str) -> Optional[Tuple[Any, dict]]:
    p = _key_to_path(key)
    if not p.exists():
        return None
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    meta_path = p.with_suffix(".meta.json")
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
    return data, meta


def compute_sha256_of_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, indent=None).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_meta(key: str) -> dict:
    """Return meta for a cache key or empty dict."""
    p = _key_to_path(key).with_suffix(".meta.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}
