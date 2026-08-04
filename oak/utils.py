"""Utility helpers used across the project."""
from typing import Iterable, List


def chunked(iterable: Iterable, size: int):
    """Yield successive chunks from iterable."""
    it = iter(iterable)
    chunk = []
    for item in it:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
