"""
OpenRussian dictionary lookup.

Downloads Russian dictionary CSV files from GitHub on first use and caches
them locally in backend/.cache.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import requests

_CACHE_DIR = Path(__file__).parent / ".cache"
_WORDS_FILE = _CACHE_DIR / "openrussian_words.csv"
_TRANSLATIONS_FILE = _CACHE_DIR / "openrussian_translations.csv"

_WORDS_URLS = [
    "https://raw.githubusercontent.com/openrussian/russian-dictionary/main/data/words.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/main/data/words.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/master/data/words.csv",
]
_TRANSLATIONS_URLS = [
    "https://raw.githubusercontent.com/openrussian/russian-dictionary/main/data/translations.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/main/data/translations.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/master/data/translations.csv",
]

# In-memory index: lowercase bare lemma -> "def1; def2; def3"
_lookup: dict[str, str] | None = None


def _download_first(urls: list[str], dest: Path) -> None:
    errors: list[str] = []
    for url in urls:
        try:
            print(f"[OpenRussian] Downloading {dest.name} from {url} ...")
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
            size_kb = dest.stat().st_size // 1024
            print(f"[OpenRussian] Saved {size_kb:,} KB -> {dest.name}")
            return
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("OpenRussian download failed; tried all mirrors:\n" + "\n".join(errors))


def _build_lookup() -> dict[str, str]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not _WORDS_FILE.exists():
        _download_first(_WORDS_URLS, _WORDS_FILE)
    if not _TRANSLATIONS_FILE.exists():
        _download_first(_TRANSLATIONS_URLS, _TRANSLATIONS_FILE)

    # Build word_id -> bare lemma map
    id_to_bare: dict[str, str] = {}
    with _WORDS_FILE.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            bare = (row.get("bare") or "").strip()
            wid = (row.get("id") or "").strip()
            if bare and wid:
                id_to_bare[wid] = bare.lower()

    # Build word_id -> English definitions map
    id_to_defs: dict[str, list[str]] = {}
    with _TRANSLATIONS_FILE.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("lang") or "").lower() != "en":
                continue
            wid = (row.get("word_id") or "").strip()
            word = (row.get("word") or "").strip()
            if wid and word:
                id_to_defs.setdefault(wid, []).append(word)

    # Merge into bare_lemma -> definition string (max 4 senses)
    result: dict[str, str] = {}
    for wid, bare in id_to_bare.items():
        defs = id_to_defs.get(wid, [])
        if defs:
            result[bare] = "; ".join(defs[:4])

    print(f"[OpenRussian] Indexed {len(result):,} words.")
    return result


def ensure_loaded() -> None:
    """Load the OpenRussian index into memory (idempotent)."""
    global _lookup
    if _lookup is None:
        _lookup = _build_lookup()


def lookup(lemma: str) -> Optional[str]:
    """Return English definition(s) for lemma or None if not found."""
    if _lookup is None:
        return None
    return _lookup.get(lemma.lower().strip())
