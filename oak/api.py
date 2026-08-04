"""Simple API client for the Oak National Academy Open API.

This client centralises HTTP behaviour: headers, retries, timeouts and basic
error handling. It now supports conditional GETs (If-None-Match) when an ETag
is available in the cache.
"""
from __future__ import annotations
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Any, Optional
import logging

from config import config
from oak import cache

logger = logging.getLogger(__name__)


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or config.api_key
        self.base_url = base_url or config.base_url
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "OakNationalDB/0.1 (+https://github.com/alexwa-alt/OakNationalDB)"
        })
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10),
           retry=retry_if_exception_type((requests.exceptions.RequestException,)))
    def get(self, path: str, params: Optional[dict] = None, timeout: int = 10) -> Any:
        """GET a JSON resource from the API.

        Path may be absolute (https://...) or relative ("/programmes/...").
        Retries network errors and some server errors.
        """
        url = path if path.startswith("http") else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        logger.debug("GET %s", url)
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            # For 4xx/5xx responses surface a helpful error
            text = resp.text if resp is not None else ""
            logger.error("HTTP error for %s: %s", url, text[:200])
            raise ApiError(f"HTTP {resp.status_code} for {url}: {text[:200]}") from e
        except requests.exceptions.RequestException as e:
            logger.error("Request failed for %s: %s", url, e)
            raise

    def get_with_cache(self, path: str, cache_key: str, params: Optional[dict] = None, timeout: int = 10) -> Any:
        """Perform a conditional GET using ETag when available in the cache.

        If the server returns 304 Not Modified we return the cached JSON. When a
        new payload is returned we also extract ETag and return the parsed JSON.
        The caller is responsible for saving the response into the cache (so
        cache metadata like sha256/last_checked are recorded).
        """
        url = path if path.startswith("http") else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        meta = cache.get_meta(cache_key)
        headers = {}
        etag = meta.get("etag")
        if etag:
            headers["If-None-Match"] = etag
        logger.debug("GET_WITH_CACHE %s (If-None-Match=%s)", url, etag)
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 304:
                # Not modified — return cached JSON
                cached = cache.load_cache(cache_key)
                if cached:
                    data, _ = cached
                    logger.debug("Cache hit (304) for %s", cache_key)
                    return data
                # If we don't have cached content fall back to raising
                resp.raise_for_status()
            resp.raise_for_status()
            # Try to capture ETag from response headers; caller should save this
            return resp.json()
        except requests.exceptions.HTTPError as e:
            text = resp.text if resp is not None else ""
            logger.error("HTTP error for %s: %s", url, text[:200])
            raise ApiError(f"HTTP {resp.status_code} for {url}: {text[:200]}") from e
        except requests.exceptions.RequestException:
            logger.exception("Request failed for %s", url)
            raise
