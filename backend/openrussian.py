"""
Russian dictionary lookup with multiple fallbacks.

Strategies:
1. Try TogetherDB OpenRussian public export (words/translations)
2. Try to download OpenRussian CSV files from GitHub mirrors
3. Try Archive.org snapshots for historical CSV copies
4. Fall back to Wiktionary API for live definitions
5. Last resort: return None (caller uses lemma as placeholder)
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import requests

_CACHE_DIR = Path(__file__).parent / ".cache"
_WORDS_FILE = _CACHE_DIR / "openrussian_words.csv"
_TRANSLATIONS_FILE = _CACHE_DIR / "openrussian_translations.csv"
_FORMS_FILE = _CACHE_DIR / "openrussian_words_forms.csv"

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
_FORMS_URLS = [
    "https://raw.githubusercontent.com/openrussian/russian-dictionary/main/data/words_forms.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/main/data/words_forms.csv",
    "https://raw.githubusercontent.com/Badestrand/russian-dictionary/master/data/words_forms.csv",
]

# In-memory index: lowercase bare lemma -> "def1; def2; def3"
_lookup: dict[str, str] | None = None
# word_id -> list[definition] — for form-based disambiguation
_wid_defs: dict[str, list[str]] | None = None
# lowercase inflected form -> word_id — built from words_forms.csv
_form_index: dict[str, str] | None = None
# Cache of Wiktionary lookups to avoid repeated API calls
_wiktionary_cache: dict[str, Optional[str]] = {}

_WIKTIONARY_API = "https://en.wiktionary.org/api/rest_v1/page/definition"

_TOGETHERDB_WORKER = "https://worker.togetherdb.com"
_TOGETHERDB_CONNECTION_ID = "fwoedz5fvtwvq03v"
_TOGETHERDB_DATABASE_NAME = "openrussian_public"


def _download_togetherdb_table(table_name: str, dest: Path) -> None:
    """Download a TogetherDB table as CSV into dest."""
    export_endpoint = (
        f"{_TOGETHERDB_WORKER}/connections/{_TOGETHERDB_CONNECTION_ID}"
        f"/databases/{_TOGETHERDB_DATABASE_NAME}/tables/{table_name}"
        "/export?expand=false&filter=&separator=%2C"
    )
    resp = requests.post(export_endpoint, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    export_key = (data.get("result") or {}).get("exportKey")
    if not export_key:
        raise ValueError(f"Missing exportKey for table '{table_name}'")

    csv_url = f"{_TOGETHERDB_WORKER}/exports/{export_key}"
    csv_resp = requests.get(csv_url, timeout=120)
    csv_resp.raise_for_status()

    tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
    tmp_dest.write_bytes(csv_resp.content)
    tmp_dest.replace(dest)


def _download_from_togetherdb() -> bool:
    """Try fetching OpenRussian CSV tables from TogetherDB worker export API."""
    try:
        structure_url = (
            f"{_TOGETHERDB_WORKER}/connections/{_TOGETHERDB_CONNECTION_ID}"
            f"/databases/{_TOGETHERDB_DATABASE_NAME}/structure"
        )
        print("[Dictionary] Checking TogetherDB OpenRussian structure...")
        structure_resp = requests.get(structure_url, timeout=30)
        structure_resp.raise_for_status()
        structure = structure_resp.json()

        table_names = {
            (t.get("name") or "").strip().lower()
            for t in (structure.get("result") or {}).get("tables", [])
            if isinstance(t, dict)
        }
        if "words" not in table_names or "translations" not in table_names:
            print(
                "[Dictionary] TogetherDB structure missing words/translations tables; "
                "continuing with other sources"
            )
            return False

        print("[Dictionary] Downloading words/translations from TogetherDB...")
        _download_togetherdb_table("words", _WORDS_FILE)
        _download_togetherdb_table("translations", _TRANSLATIONS_FILE)
        # Also download forms table if present (non-fatal if missing)
        if "words_forms" in table_names:
            try:
                _download_togetherdb_table("words_forms", _FORMS_FILE)
                forms_kb = _FORMS_FILE.stat().st_size // 1024
                print(f"[Dictionary] Saved words_forms: {forms_kb:,} KB")
            except Exception as exc:
                print(f"[Dictionary] words_forms download skipped: {exc}")
        words_kb = _WORDS_FILE.stat().st_size // 1024
        trans_kb = _TRANSLATIONS_FILE.stat().st_size // 1024
        print(
            f"[Dictionary] Saved TogetherDB CSV cache: "
            f"words={words_kb:,} KB, translations={trans_kb:,} KB"
        )
        return True
    except Exception as exc:
        print(f"[Dictionary] TogetherDB export failed: {exc}")
        return False


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


def _query_wiktionary_russian(lemma: str) -> Optional[str]:
    """Query Russian Wiktionary (ru.wiktionary.org) for native Russian definitions.
    
    Falls back to English Wiktionary if Russian definitions unavailable.
    """
    if lemma in _wiktionary_cache:
        return _wiktionary_cache[lemma]
    
    try:
        # Try Russian Wiktionary first
        url = f"https://ru.wiktionary.org/api/rest_v1/page/definition/{lemma}"
        headers = {
            "User-Agent": "Flowup Russian Learning App (https://github.com/your-repo)",
        }
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            try:
                data = r.json()
                # Try to extract definitions from Russian Wiktionary
                if isinstance(data, dict):
                    # Russian Wiktionary returns definitions in "en" or "ru" key
                    defs = []
                    for lang_key in ["en", "ru"]:
                        if lang_key in data:
                            for entry in data.get(lang_key, []):
                                if isinstance(entry, dict) and "definitions" in entry:
                                    for def_obj in entry["definitions"]:
                                        if isinstance(def_obj, dict) and "definition" in def_obj:
                                            html_def = def_obj["definition"]
                                            plain_def = _extract_text_from_html(html_def)
                                            if plain_def:
                                                defs.append(plain_def)
                    if defs:
                        result = "; ".join(defs[:3])
                        _wiktionary_cache[lemma] = result
                        return result
            except (ValueError, KeyError, IndexError):
                pass  # Fall through to English Wiktionary
    except Exception:
        pass  # Silent fail, try English Wiktionary
    
    # Fall back to English Wiktionary if Russian unavailable
    return _query_wiktionary(lemma)

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


def _build_lookup(already_tried_download: bool = False) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """Return (lemma_lookup, wid_defs, form_index).

    lemma_lookup : bare_lemma -> "; "-joined definitions (all homographs merged,
                   higher-frequency word's senses listed first).
    wid_defs     : word_id   -> list[definition] for direct word_id access.
    form_index   : inflected_form -> word_id (from words_forms.csv, if available).
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    result: dict[str, str] = {}
    wid_defs: dict[str, list[str]] = {}
    form_index: dict[str, str] = {}

    # Try to load from local cache
    if _WORDS_FILE.exists() and _TRANSLATIONS_FILE.exists():
        try:
            # Build word_id -> (bare lemma, rank) map
            id_to_bare: dict[str, str] = {}
            id_to_rank: dict[str, int] = {}
            with _WORDS_FILE.open(encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    bare = (row.get("bare") or "").strip()
                    wid = (row.get("id") or "").strip()
                    if bare and wid:
                        id_to_bare[wid] = bare.lower()
                        rank_raw = (row.get("rank") or "").strip()
                        id_to_rank[wid] = int(rank_raw) if rank_raw.isdigit() else 999_999

            # Build word_id -> English definitions map
            id_to_defs: dict[str, list[str]] = {}
            with _TRANSLATIONS_FILE.open(encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if (row.get("lang") or "").lower() != "en":
                        continue
                    wid = (row.get("word_id") or "").strip()
                    # TogetherDB/OpenRussian exports use `tl` for translated text.
                    word = (row.get("word") or row.get("tl") or "").strip()
                    if wid and word:
                        id_to_defs.setdefault(wid, []).append(word)

            # Build wid_defs
            for wid, defs in id_to_defs.items():
                wid_defs[wid] = defs[:4]

            # Merge into bare_lemma -> definition string.
            # When multiple word_ids share the same bare lemma (homographs), collect
            # all their definitions ordered by word frequency rank (lower = more common),
            # so the primary meaning comes first rather than being overwritten.
            from collections import defaultdict
            bare_to_wids: dict[str, list[str]] = defaultdict(list)
            for wid, bare in id_to_bare.items():
                bare_to_wids[bare].append(wid)

            for bare, wids in bare_to_wids.items():
                # Sort by rank ascending (most common word first)
                sorted_wids = sorted(wids, key=lambda w: id_to_rank.get(w, 999_999))
                merged: list[str] = []
                for wid in sorted_wids:
                    merged.extend(id_to_defs.get(wid, [])[:2])  # up to 2 senses per homograph
                if merged:
                    result[bare] = "; ".join(merged[:4])

            # Build form index from words_forms.csv if available
            if _FORMS_FILE.exists():
                try:
                    with _FORMS_FILE.open(encoding="utf-8-sig") as fh:
                        reader = csv.DictReader(fh)
                        for row in reader:
                            wid = (row.get("word_id") or "").strip()
                            form = (row.get("form") or "").strip().lower()
                            if wid and form and wid in id_to_bare:
                                form_index[form] = wid
                    print(f"[Dictionary] Forms index: {len(form_index):,} entries.")
                except Exception as exc:
                    print(f"[Dictionary] Could not load forms index: {exc}")

            print(f"[Dictionary] Loaded {len(result):,} words from local cache.")
            return result, wid_defs, form_index
        except Exception as exc:
            print(f"[Dictionary] Failed to read local cache: {exc}")
            return {}, {}, {}
    
    # Try to download fresh copies on first call (not on recursive call)
    if not already_tried_download:
        # First try TogetherDB worker export (currently most reliable OpenRussian source)
        if _download_from_togetherdb():
            return _build_lookup(already_tried_download=True)

        # First try GitHub mirrors
        try:
            _download_first(_WORDS_URLS, _WORDS_FILE)
            _download_first(_TRANSLATIONS_URLS, _TRANSLATIONS_FILE)
            # Also attempt forms file (non-fatal if unavailable)
            try:
                _download_first(_FORMS_URLS, _FORMS_FILE)
            except Exception:
                pass
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
    return {}, {}, {}


def ensure_loaded() -> None:
    """Load the dictionary index into memory (idempotent)."""
    global _lookup, _wid_defs, _form_index
    if _lookup is None:
        _lookup, _wid_defs, _form_index = _build_lookup()


def lookup_all(lemma: str) -> list[str]:
    """Return all English definitions for lemma as a list.

    Strips combining accent marks before lookup so stressed lemmas (е.г. пи́сать)
    correctly resolve to the bare entry.  When multiple homographs share the same
    bare form their definitions are already merged in frequency order by
    _build_lookup.  Falls back to Wiktionary when not in the local cache.
    """
    if _lookup is None:
        return []
    # Strip only combining acute accent (U+0301 — stress mark) so "пи́сать" → "писать"
    # Do NOT strip all combining chars: й decomposes into и + U+0306 (breve) in NFD,
    # so a blanket strip would corrupt keys like нужный → нужныи.
    import unicodedata
    bare = unicodedata.normalize("NFD", lemma.lower().strip())
    bare = unicodedata.normalize("NFC", "".join(c for c in bare if c != "\u0301"))
    combined = _lookup.get(bare)
    if combined is None:
        combined = _query_wiktionary_russian(bare)
    if not combined:
        return []
    return [d.strip() for d in combined.split(";") if d.strip()]


def lookup_all_by_form(raw_form: str) -> list[str]:
    """Return definitions by looking up the original inflected form directly.

    Uses the words_forms.csv index to find the exact word_id for `raw_form`,
    which unambiguously picks the right homograph.  For example, 'пишет'
    maps to the word_id for писать (to write), not пи́сать (to piss), because
    'пишет' is not a valid form of the latter.

    Returns an empty list when the form index is unavailable or the form is
    not found; the caller should then fall back to `lookup_all(lemma)`.
    """
    if not _form_index or not _wid_defs:
        return []
    import unicodedata
    bare = unicodedata.normalize("NFD", raw_form.lower().strip())
    bare = unicodedata.normalize("NFC", "".join(c for c in bare if c != "\u0301"))
    wid = _form_index.get(bare)
    if not wid:
        return []
    return list(_wid_defs.get(wid, []))


def lookup_local(lemma: str) -> Optional[str]:
    """Return English definition(s) for lemma using only the local in-memory dict.

    Never makes network calls — guaranteed O(1).  Use this in the serve-time
    path (e.g. _enrich_definition) so GET /songs/{id} never blocks on Wiktionary.
    """
    if _lookup is None:
        return None
    import unicodedata
    bare = unicodedata.normalize("NFD", lemma.lower().strip())
    bare = unicodedata.normalize("NFC", "".join(c for c in bare if c != "\u0301"))
    combined = _lookup.get(bare)
    if not combined:
        return None
    defs = [d.strip() for d in combined.split(";") if d.strip()]
    return "; ".join(defs) if defs else None


def lookup(lemma: str) -> Optional[str]:
    """Return English definition(s) for lemma.
    
    Strategy:
    1. Check local in-memory cache (loaded from TogetherDB/GitHub/Archive CSV)
    2. Try Russian Wiktionary API for live lookup
    3. Fall back to English Wiktionary API
    4. Return None if all fail (caller uses lemma as placeholder)
    """
    defs = lookup_all(lemma)
    return "; ".join(defs) if defs else None
