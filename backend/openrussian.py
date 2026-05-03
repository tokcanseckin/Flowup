"""
Russian dictionary lookup with multiple fallbacks.

Strategies:
1. Try to download OpenRussian CSV files from GitHub (cached locally)
2. Fall back to Wiktionary API for live definitions
3. Last resort: return None (caller uses lemma as placeholder)
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
# Cache of Wiktionary lookups to avoid repeated API calls
_wiktionary_cache: dict[str, Optional[str]] = {}

_WIKTIONARY_API = "https://en.wiktionary.org/api/rest_v1/page/definition"


def _download_first(urls: list[str], dest: Path) -> None:
    """Try downloading from multiple URLs. Non-fatal if all fail."""
    errors: list[str] = []
    for url in urls:
        try:
            print(f"[Dictionary] Downloading {dest.name} from {url} ...")
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
            size_kb = dest.stat().st_size // 1024
            print(f"[Dictionary] Saved {size_kb:,} KB -> {dest.name}")
            return
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    # Non-fatal: warn and continue to fallback strategy
    print(f"[Dictionary] CSV download failed (will try Archive.org):\n{', '.join(errors)}")


def _download_from_archive_org(urls: list[str], dest: Path) -> bool:
    """Try downloading cached version from Archive.org Wayback Machine.
    
    Returns True if successful, False otherwise.
    """
    for url in urls:
        try:
            # Query Archive.org for closest snapshot
            archive_api = f"https://archive.org/wayback/available?url={url}"
            print(f"[Dictionary] Checking Archive.org for {dest.name} snapshot...")
            r = requests.get(archive_api, timeout=10)
            if r.status_code != 200:
                continue
            
            data = r.json()
            snapshots = data.get("archived_snapshots", {})
            if not snapshots:
                continue
            
            closest = snapshots.get("closest", {})
            snapshot_url = closest.get("url")
            timestamp = closest.get("timestamp", "?")
            
            if not snapshot_url:
                continue
            
            print(f"[Dictionary] Found archived snapshot from {timestamp}")
            print(f"[Dictionary] Downloading from {snapshot_url}...")
            
            r = requests.get(snapshot_url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
            size_kb = dest.stat().st_size // 1024
            print(f"[Dictionary] Saved {size_kb:,} KB from Archive.org -> {dest.name}")
            return True
            
        except Exception as exc:
            print(f"[Dictionary] Archive.org snapshot failed for {url}: {exc}")
            continue
    
    print("[Dictionary] Archive.org snapshots unavailable")
    return False


def _extract_text_from_html(html: str) -> str:
    """Extract plain text from Wiktionary HTML definitions."""
    import re
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', html)
    # Decode HTML entities
    text = text.replace('&quot;', '"').replace('&amp;', '&').replace('&#39;', "'")
    return text.strip()


def _query_wiktionary(lemma: str) -> Optional[str]:
    """Query Wiktionary API for English definition of Russian word."""
    if lemma in _wiktionary_cache:
        return _wiktionary_cache[lemma]
    
    try:
        url = f"{_WIKTIONARY_API}/{lemma}"
        # Wiktionary API requires a User-Agent header
        headers = {
            "User-Agent": "Flowup Russian Learning App (https://github.com/your-repo)",
        }
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 404:
            _wiktionary_cache[lemma] = None
            return None
        r.raise_for_status()
        data = r.json()
        
        # Extract English definitions from Wiktionary JSON response
        # Format: {"ru": [{"definitions": [{"definition": "<html>def</html>"}, ...], ...}], ...}
        if isinstance(data, dict):
            ru_entries = data.get("ru", [])
            if ru_entries:
                defs = []
                for entry in ru_entries:
                    if isinstance(entry, dict) and "definitions" in entry:
                        for def_obj in entry["definitions"]:
                            if isinstance(def_obj, dict) and "definition" in def_obj:
                                html_def = def_obj["definition"]
                                plain_def = _extract_text_from_html(html_def)
                                if plain_def:
                                    defs.append(plain_def)
                if defs:
                    result = "; ".join(defs[:3])  # Max 3 definitions
                    _wiktionary_cache[lemma] = result
                    return result
        
        _wiktionary_cache[lemma] = None
        return None
    except Exception as exc:
        print(f"[Dictionary] Wiktionary lookup failed for '{lemma}': {exc}")
        _wiktionary_cache[lemma] = None
        return None


def _build_lookup(already_tried_download: bool = False) -> dict[str, str]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    result: dict[str, str] = {}
    
    # Try to load from local cache
    if _WORDS_FILE.exists() and _TRANSLATIONS_FILE.exists():
        try:
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
            for wid, bare in id_to_bare.items():
                defs = id_to_defs.get(wid, [])
                if defs:
                    result[bare] = "; ".join(defs[:4])
            
            print(f"[Dictionary] Loaded {len(result):,} words from local cache.")
            return result
        except Exception as exc:
            print(f"[Dictionary] Failed to read local cache: {exc}")
            return {}
    
    # Try to download fresh copies on first call (not on recursive call)
    if not already_tried_download:
        # First try GitHub mirrors
        try:
            _download_first(_WORDS_URLS, _WORDS_FILE)
            _download_first(_TRANSLATIONS_URLS, _TRANSLATIONS_FILE)
            # Now try loading from the files we just downloaded
            return _build_lookup(already_tried_download=True)
        except Exception as exc:
            print(f"[Dictionary] GitHub CSV download failed: {exc}")
        
        # If GitHub mirrors failed, try Archive.org snapshots
        archive_success_words = _download_from_archive_org(_WORDS_URLS, _WORDS_FILE)
        archive_success_trans = _download_from_archive_org(_TRANSLATIONS_URLS, _TRANSLATIONS_FILE)
        
        if archive_success_words and archive_success_trans:
            # Successfully downloaded from Archive.org, now load
            return _build_lookup(already_tried_download=True)
        elif archive_success_words or archive_success_trans:
            print("[Dictionary] Partial Archive.org recovery (will use Wiktionary for fallback)")
    
    print("[Dictionary] Will use Wiktionary API for live lookups.")
    return {}


def ensure_loaded() -> None:
    """Load the dictionary index into memory (idempotent)."""
    global _lookup
    if _lookup is None:
        _lookup = _build_lookup()


def lookup(lemma: str) -> Optional[str]:
    """Return English definition(s) for lemma.
    
    Strategy:
    1. Check local in-memory cache (loaded from CSV)
    2. Try Wiktionary API for live lookup
    3. Return None if all fail (caller uses lemma as placeholder)
    """
    if _lookup is None:
        return None
    
    result = _lookup.get(lemma.lower().strip())
    if result is not None:
        return result
    
    # Try live Wiktionary lookup as fallback
    return _query_wiktionary(lemma.lower().strip())
