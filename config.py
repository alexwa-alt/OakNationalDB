from dataclasses import dataclass
from pathlib import Path
import os
from typing import Optional


@dataclass
class Config:
    """Configuration for OakNationalDB. Values are read from environment variables
    by default so that sensitive values (like API keys) are not stored in source.

    Set OAK_API_KEY in your environment once you receive it.
    """

    api_key: Optional[str] = os.getenv("OAK_API_KEY")
    base_url: str = os.getenv("OAK_BASE_URL", "https://www.thenational.academy/api/v1")
    cache_dir: Path = Path(os.getenv("OAK_CACHE_DIR", "data/cache"))
    db_path: Path = Path(os.getenv("OAK_DB_PATH", "database/curriculum.db"))


config = Config()
