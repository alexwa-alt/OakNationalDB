"""Simple API client for the Oak National Academy Open API.

This client centralises HTTP behaviour: headers, retries, timeouts and basic
error handling. It deliberately does not implement domain-specific parsing —
that belongs in the importer.
"""
from __future__ import annotations
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Any, Optional
import logging

from config import config

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

    # Domain convenience methods can be added here later (e.g. list_programmes, get_unit)
