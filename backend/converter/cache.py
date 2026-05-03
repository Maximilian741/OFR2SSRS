"""Conversion result cache keyed by SHA-256 of input bytes.

Memoizes convert() results to avoid redundant work when batch-processing
many similar Oracle reports. Optionally persists to disk as JSON files.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Callable, Optional


class ConversionCache:
    """Thread-safe cache for conversion results, keyed by SHA-256 of input bytes.

    If ``cache_dir`` is provided, results are persisted to disk as JSON files
    named ``<sha256>.json``. Otherwise, the cache lives only in memory.
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._lock = Lock()
        self._mem: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
        self._cache_dir: Optional[Path] = None
        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def key(self, xml_bytes: bytes) -> str:
        """Return SHA-256 hex digest of input bytes."""
        return hashlib.sha256(xml_bytes).hexdigest()

    def _disk_path(self, k: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{k}.json"

    def get(self, xml_bytes: bytes) -> Optional[dict]:
        """Return cached result for ``xml_bytes`` or None on miss.

        Updates hit/miss counters. Disk-persisted entries are lazily promoted
        into memory on access.
        """
        k = self.key(xml_bytes)
        with self._lock:
            if k in self._mem:
                self._hits += 1
                return self._mem[k]

            disk = self._disk_path(k)
            if disk is not None and disk.exists():
                try:
                    with disk.open("r", encoding="utf-8") as f:
                        result = json.load(f)
                    self._mem[k] = result
                    self._hits += 1
                    return result
                except (OSError, json.JSONDecodeError):
                    # Corrupt or unreadable; treat as miss and clean up.
                    try:
                        disk.unlink()
                    except OSError:
                        pass

            self._misses += 1
            return None

    def set(self, xml_bytes: bytes, result: dict) -> None:
        """Store ``result`` under SHA-256 of ``xml_bytes``."""
        k = self.key(xml_bytes)
        with self._lock:
            self._mem[k] = result
            disk = self._disk_path(k)
            if disk is not None:
                tmp = disk.with_suffix(disk.suffix + ".tmp")
                try:
                    with tmp.open("w", encoding="utf-8") as f:
                        json.dump(result, f)
                    os.replace(tmp, disk)
                except (OSError, TypeError):
                    # Result not JSON-serializable or disk write failed;
                    # keep the in-memory entry and drop the temp file.
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except OSError:
                        pass

    def stats(self) -> dict:
        """Return ``{hits, misses, size}`` snapshot."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._mem),
            }


def cached_convert(
    xml_bytes: bytes,
    cache: ConversionCache,
    convert_fn: Callable[[bytes], dict],
) -> dict:
    """Look up ``xml_bytes`` in ``cache``; on miss call ``convert_fn`` and store."""
    hit = cache.get(xml_bytes)
    if hit is not None:
        return hit
    result = convert_fn(xml_bytes)
    cache.set(xml_bytes, result)
    return result
