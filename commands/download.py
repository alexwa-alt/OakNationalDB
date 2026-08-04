"""Download command: prepares client, iterates over programmes/units and caches raw JSON.

Currently the real API calls are guarded so the command can be run before an API
key is provided. Once you set OAK_API_KEY environment variable the script will
perform requests and populate data/cache/ and the database.
"""
import logging
from config import config
from oak.api import ApiClient, ApiError
from oak import cache
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    print("OakNationalDB downloader")
    client = ApiClient()
    if not client.api_key:
        print("No API key configured (OAK_API_KEY). The downloader will not call the API.")
        print("Once you set OAK_API_KEY the script will fetch data and cache responses under data/cache/")
        return

    # Example flow (the importer will take JSON and translate into dataclasses + DB)
    try:
        # The real API paths and response shapes will be implemented in importer.py
        programmes = client.get("/programmes?exam_board=aqa&phase=secondary")
        cache.save_cache("programmes_aqa_secondary", programmes)
        print("Saved programmes list to cache")
    except ApiError as e:
        logger.error("API error: %s", e)


if __name__ == "__main__":
    main()
