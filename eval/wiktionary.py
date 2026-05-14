"""
Turkish definitions for Russian words — two-source lookup with fallback.

Primary (single-hop):
  tr.wiktionary.org  — Russian word → ==Rusça== section → [[Turkish]] wikilinks

Fallback (two-hop, used when primary returns empty):
  en.wiktionary.org REST API  — Russian word → English glosses
  en.wiktionary.org MediaWiki API  — English gloss → {{t|tr|…}} templates → Turkish

Results are cached in eval/data/wikt_cache.db.

Usage:
    from eval.wiktionary import WiktionaryLookup
    lk = WiktionaryLookup()
    lk.lookup("собака", grammar="noun, feminine")  # → ["köpek", "it"]
    lk.lookup("я",      grammar="pronoun, personal")  # → ["ben"]
    lk.close()
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DB = Path(__file__).parent / "data" / "wikt_cache.db"
TR_WIKI_API = "https://tr.wiktionary.org/w/api.php"
EN_REST_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"
EN_WIKI_API = "https://en.wiktionary.org/w/api.php"

# Matches [[word]] or [[word|display]] wikilinks — captures the target word
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")
# Matches {{t|tr|WORD}}, {{t+|tr|WORD}}, etc. translation templates
_TRANS_RE = re.compile(r"\{\{t[+*-]?\|tr\|([^|}]+)")
# HTML helpers for en.wiktionary.org REST definitions
_TITLE_RE = re.compile(r'<a[^>]+title="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")

_STRIP_CHARS = ".,!?;:—–-«»\"'"

# POS tags that represent a character/letter, not a word — skip in en.wikt
_LETTER_POS = {"letter", "character", "symbol", "diacritic", "punctuation mark"}

# Turkish Wiktionary POS section header names → coarse English POS
# (the section headers appear as ===Ad===, ===Eylem===, etc.)
_TR_POS_TO_COARSE: dict[str, str] = {
    "ad":         "noun",
    "adıl":       "pronoun",
    "eylem":      "verb",
    "sıfat":      "adjective",
    "zarf":       "adverb",
    "belirteç":   "adverb",
    "edat":       "preposition",
    "bağlaç":     "conjunction",
    "ünlem":      "interjection",
    "sayı":       "numeral",
    "sözcük":     "particle",
    "özel ad":    "noun",
    "ön ad":      "adjective",
}

# Maps keywords from backend grammar strings to coarse POS labels
# (adverb before verb — "verb" is a substring of "adverb")
_GRAMMAR_TO_COARSE: list[tuple[str, str]] = [
    ("pronoun",      "pronoun"),
    ("adverb",       "adverb"),
    ("verb",         "verb"),
    ("adjective",    "adjective"),
    ("noun",         "noun"),
    ("preposition",  "preposition"),
    ("conjunction",  "conjunction"),
    ("particle",     "particle"),
    ("numeral",      "numeral"),
    ("interjection", "interjection"),
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _strip_stress(text: str) -> str:
    # Only remove the combining acute accent (U+0301) used for Russian stress
    # marks. Other combining characters (e.g. the breve in й) must stay so
    # letters like й (и+breve) recompose correctly via NFC.
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if c != "\u0301")
    return unicodedata.normalize("NFC", stripped)


def _normalise(lemma: str) -> str:
    s = _strip_stress(lemma).lower().strip(_STRIP_CHARS)
    # pymorphy sometimes lemmatizes to the Church Slavonic -ие variant
    # (e.g. счастьем → счастие) while Wiktionary only has the modern -ье form.
    # Normalise -ие → -ье at word-end so cache keys and lookups stay consistent.
    if s.endswith("ие"):
        s = s[:-2] + "ье"
    return s


def _coarse_pos(grammar: str) -> str:
    """Map a backend grammar string to a coarse POS label (e.g. 'pronoun')."""
    g = grammar.lower()
    for keyword, pos in _GRAMMAR_TO_COARSE:
        if keyword in g:
            return pos
    return ""


def _extract_wikilinks(text: str) -> list[str]:
    """Return unique [[wikilink]] targets from a block of wikitext."""
    result: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(text):
        word = m.group(1).strip()
        # Skip meta-links (File:, Category:, etc.) and empty/punctuation-only
        if ":" in word or not any(c.isalpha() for c in word):
            continue
        if word not in seen:
            result.append(word)
            seen.add(word)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# tr.wiktionary.org lookup
# ──────────────────────────────────────────────────────────────────────────────

def _wiki_get(api_url: str, params: dict, retries: int = 3) -> dict:
    """GET a Wikimedia API endpoint with exponential-backoff retry on HTTP 429."""
    import urllib.error
    encoded = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{api_url}?{encoded}",
        headers={"User-Agent": "FlowupEval/1.0 (translation quality evaluation tool)"},
    )
    delay = 5.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
            else:
                raise
    return {}


def _fetch_from_tr_wikt(word: str, preferred_pos: str = "") -> list[str]:
    """
    Look up *word* on tr.wiktionary.org and return Turkish definition words.

    Finds the ==Rusça== section, then extracts [[wikilinks]] from definition
    lines.  When *preferred_pos* is given, definitions from the matching POS
    subsection are returned first, with other subsections appended as fallback.
    """
    params = {
        "action": "query",
        "titles": word,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }
    data = _wiki_get(TR_WIKI_API, params)

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return []
    revisions = pages[0].get("revisions", [])
    if not revisions:
        return []
    wikitext = revisions[0].get("slots", {}).get("main", {}).get("content", "")
    if not wikitext:
        return []

    # ── Isolate the ==Rusça== section ────────────────────────────────────────
    # Split on level-2 headers (== ... ==)
    l2_split = re.split(r"(?m)^==([^=].*?)==\s*$", wikitext)
    # l2_split: [before, header1, body1, header2, body2, ...]
    rusca_body = ""
    it = iter(l2_split)
    next(it)  # skip text before first header
    for header in it:
        body = next(it, "")
        if header.strip().lower() == "rusça":
            rusca_body = body
            break

    if not rusca_body:
        return []

    # ── Split Rusça body into POS subsections (=== ... ===) ─────────────────
    l3_split = re.split(r"(?m)^===([^=].*?)===\s*$", rusca_body)
    # l3_split: [before_first_subsection, header1, body1, ...]

    preferred_links: list[str] = []
    fallback_links: list[str] = []
    seen: set[str] = set()

    def _add_links(text: str, target: list[str]) -> None:
        for w in _extract_wikilinks(text):
            # Skip Cyrillic wikilinks — the ==Rusça== section sometimes links
            # to Russian inflected forms (e.g. полу́ночи) which are not TR words.
            if any("\u0400" <= c <= "\u04FF" for c in w):
                continue
            if w not in seen:
                target.append(w)
                seen.add(w)

    it3 = iter(l3_split)
    preamble = next(it3, "")
    _add_links(preamble, fallback_links)  # text before any === subsection

    for sub_header in it3:
        sub_body = next(it3, "")
        tr_pos = _TR_POS_TO_COARSE.get(sub_header.strip().lower(), "")
        is_preferred = bool(preferred_pos) and tr_pos == preferred_pos
        _add_links(sub_body, preferred_links if is_preferred else fallback_links)

    return preferred_links + fallback_links


# ──────────────────────────────────────────────────────────────────────────────
# en.wiktionary.org two-hop fallback
# ──────────────────────────────────────────────────────────────────────────────

def _clean_def(definition_html: str) -> str:
    """Extract a plain English gloss from a Wiktionary HTML definition string.

    Uses plain text (HTML-stripped) as the primary result so that multi-word
    phrases like "stretched ear piercing, flesh tunnel" retain their comma
    boundaries when the caller splits on commas.  Link-title extraction is
    only used as a last resort when the stripped text is empty.
    """
    plain = html.unescape(_TAG_RE.sub("", definition_html)).strip()
    if len(plain) >= 2 and any(c.isalpha() for c in plain):
        return plain
    # Fallback: gather link titles when plain text is empty/unusable
    titles = [
        html.unescape(t).strip()
        for t in _TITLE_RE.findall(definition_html)
        if ":" not in t and len(t) >= 2 and any(c.isalpha() for c in t)
    ]
    if titles:
        seen: set[str] = set()
        unique = [t for t in titles if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]
        return ", ".join(unique)
    return ""


def _fetch_en_glosses(word: str, preferred_pos: str = "") -> list[str]:
    """Fetch English glosses for a Russian word from en.wiktionary.org REST API."""
    encoded = urllib.parse.quote(word)
    req = urllib.request.Request(
        EN_REST_URL.format(word=encoded),
        headers={"User-Agent": "FlowupEval/1.0 (translation quality evaluation tool)"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    preferred: list[str] = []
    fallback: list[str] = []
    seen: set[str] = set()

    for section_key in ("ru", "other"):
        for pos_entry in data.get(section_key, []):
            pos = pos_entry.get("partOfSpeech", "").strip().lower()
            if pos in _LETTER_POS:
                continue
            is_preferred = bool(preferred_pos) and pos == preferred_pos
            for defn in pos_entry.get("definitions", []):
                gloss = _clean_def(defn.get("definition", ""))
                if gloss and gloss not in seen and len(gloss) < 120:
                    (preferred if is_preferred else fallback).append(gloss)
                    seen.add(gloss)
        if preferred or fallback:
            break

    return preferred + fallback


def _fetch_tr_for_en_word(en_word: str, preferred_pos: str = "") -> list[str]:
    """Look up an English word on tr.wiktionary.org (==İngilizce== section) and return Turkish wikilinks.

    When *preferred_pos* is given, links from the matching POS subsection are
    returned first; remaining subsections are appended as fallback.
    """
    params = {
        "action": "query",
        "titles": en_word,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }
    data = _wiki_get(TR_WIKI_API, params)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return []
    revisions = pages[0].get("revisions", [])
    if not revisions:
        return []
    wikitext = revisions[0].get("slots", {}).get("main", {}).get("content", "")

    # Find ==İngilizce== section (handle İ vs I encoding)
    l2_split = re.split(r"(?m)^==([^=].*?)==\s*$", wikitext)
    ing_body = ""
    it = iter(l2_split)
    next(it)  # skip text before first header
    for header in it:
        body = next(it, "")
        if "ngilizce" in header.strip():  # matches İngilizce / Ingilizce
            ing_body = body
            break
    if not ing_body:
        return []

    # Noise sets for filtering wikilinks
    _TR_GRAMMAR_NOISE = {
        "edat", "bağlaç", "ünlem", "adıl", "önek", "sonek", "sözcük",
        "eylem", "sıfat", "zarf", "ad", "sayı", "belirteç",
    }
    # Non-POS section headers to skip entirely (etymology, references, synonyms, etc.)
    _TR_NONPOS_SECTIONS = {
        "köken", "etimoloji", "kaynakça", "referanslar", "notlar",
        "ayrıca bakınız", "ilgili sözcükler", "çeviriler", "bağlantılar",
        "görüntüler", "telaffuz", "anlamlar", "anagramlar", "söyleniş",
        "eş anlamlılar", "karşıt anlamlılar",
    }

    def _filter(words: list[str]) -> list[str]:
        out = []
        for w in words:
            if len(w) < 2:
                continue
            if any("\u0400" <= c <= "\u04FF" for c in w):
                continue
            if w.lower() in _TR_GRAMMAR_NOISE:
                continue
            parts = w.split()
            if len(parts) >= 2 and parts[1][0].isupper():
                continue
            # Skip abbreviations: all uppercase, 2-3 chars (e.g. "BK", "ABD")
            if w.isupper() and len(w) <= 3:
                continue
            out.append(w)
        return out

    # Split ==İngilizce== body into POS subsections (=== ... ===)
    l3_split = re.split(r"(?m)^===([^=].*?)===\s*$", ing_body)

    # Three tiers:
    #   preferred  — matching POS section
    #   other      — non-verb sections (nouns, adjectives, etc.)
    #   verb_only  — verb sections
    # When preferred_pos is a non-verb, verb_only is only appended as a last
    # resort (i.e. when preferred + other are both empty), preventing Turkish
    # verb infinitives (-mak/-mek forms) from polluting adjective/noun results.
    preferred_links: list[str] = []
    other_links: list[str] = []
    verb_links: list[str] = []
    seen: set[str] = set()

    def _add_links(text: str, target: list[str]) -> None:
        for w in _filter(_extract_wikilinks(text)):
            if w not in seen:
                target.append(w)
                seen.add(w)

    def _strip_l4_nonpos(body: str) -> str:
        """Remove level-4 (====) subsections whose headers are in _TR_NONPOS_SECTIONS."""
        parts = re.split(r"(?m)^====([^=].*?)====\s*$", body)
        it4 = iter(parts)
        kept = [next(it4, "")]
        for h4 in it4:
            body4 = next(it4, "")
            if h4.strip().lower() not in _TR_NONPOS_SECTIONS:
                kept.append(body4)
        return "\n".join(kept)

    it3 = iter(l3_split)
    preamble = next(it3, "")
    _add_links(_strip_l4_nonpos(preamble), other_links)  # text before any === subsection

    for sub_header in it3:
        sub_body = next(it3, "")
        h = sub_header.strip().lower()
        # Skip etymology, references, and other non-definition sections
        if h in _TR_NONPOS_SECTIONS:
            continue
        tr_pos = _TR_POS_TO_COARSE.get(h, "")
        clean_body = _strip_l4_nonpos(sub_body)
        if bool(preferred_pos) and tr_pos == preferred_pos:
            _add_links(clean_body, preferred_links)
        elif tr_pos == "verb":
            _add_links(clean_body, verb_links)
        else:
            _add_links(clean_body, other_links)

    core = preferred_links + other_links
    # Append verb results only when: no preferred_pos set, preferred_pos is
    # "verb", or there are no non-verb results at all (verb-only is better than []).
    if not preferred_pos or preferred_pos == "verb" or not core:
        return core + verb_links
    return core


def _fetch_tr_from_english(en_word: str) -> list[str]:
    """Look for a ==Turkish== language section on en.wiktionary for en_word.

    Only words that are *also* Turkish words have a ==Turkish== section on
    en.wiktionary (e.g. loanwords like "metro", "park"). We extract wikilinks
    from that section as Turkish word forms. This avoids the noise produced by
    scraping hidden English→Turkish translation tables (which belong to the
    English section and are sense-agnostic).
    """
    params = {
        "action": "query",
        "titles": en_word,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }
    data = _wiki_get(EN_WIKI_API, params)

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return []
    revisions = pages[0].get("revisions", [])
    if not revisions:
        return []
    wikitext = revisions[0].get("slots", {}).get("main", {}).get("content", "")

    # Split by level-2 headers (==Header==) and find the ==Turkish== section
    l2_parts = re.split(r"(?m)^==([^=].*?)==\s*$", wikitext)
    it = iter(l2_parts)
    next(it, "")  # skip preamble before first header
    turkish_body = ""
    for lang_header in it:
        body = next(it, "")
        if lang_header.strip().lower() == "turkish":
            turkish_body = body
            break

    if not turkish_body:
        return []

    # Extract wikilinks [[word]] or [[word|display]] from the Turkish section
    result: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[\[([^\]|#]+)", turkish_body):
        w = m.group(1).strip()
        if w and w not in seen and any(c.isalpha() for c in w):
            result.append(w)
            seen.add(w)
    return result


def _fetch_via_en_wikt(word: str, preferred_pos: str = "") -> list[str]:
    """Two-hop fallback: Russian word → en.wiktionary glosses → tr.wiktionary → Turkish."""
    try:
        glosses = _fetch_en_glosses(word, preferred_pos=preferred_pos)
        time.sleep(2.0)
    except Exception:
        return []

    seen: set[str] = set()
    result: list[str] = []
    for gloss in glosses[:3]:
        # Split gloss on comma/semicolon to get individual English terms
        # e.g. "too, too much" → ["too", "too much"]
        terms = [t.strip() for t in re.split(r"[,;]", gloss) if t.strip()]
        for term in terms[:3]:
            # Skip any term that contains Cyrillic — it's not an English pivot word
            # (can happen when _clean_def extracts a link title from a Russian gloss)
            if any("\u0400" <= c <= "\u04FF" for c in term):
                continue
            # Primary: look up the English term on tr.wiktionary (==İngilizce==)
            try:
                candidates = _fetch_tr_for_en_word(term, preferred_pos=preferred_pos)
                time.sleep(2.0)
            except Exception:
                candidates = []
            # Fallback: try en.wiktionary's ==Turkish== section for this term.
            # Only words that are also Turkish (loanwords etc.) will have this section.
            # Avoids noise from English→Turkish translation tables.
            if not candidates:
                try:
                    candidates = _fetch_tr_from_english(term)
                    time.sleep(2.0)
                except Exception:
                    candidates = []
            for w in candidates:
                # Final guard: skip any Cyrillic that slipped through
                if any("\u0400" <= c <= "\u04FF" for c in w):
                    continue
                if w not in seen:
                    result.append(w)
                    seen.add(w)
        if len(result) >= 3:
            break  # enough translations found; stop trying more glosses
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Cache DB
# ──────────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS cache (
    word       TEXT PRIMARY KEY,
    tr_words   TEXT NOT NULL,   -- JSON list of Turkish words/phrases (may be [])
    fetched_at INTEGER NOT NULL
);
"""


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)


# ──────────────────────────────────────────────────────────────────────────────
# Public interface
# ──────────────────────────────────────────────────────────────────────────────

class WiktionaryLookup:
    """
    Cache-backed lookup for Russian → Turkish definitions.

    For each Russian lemma:
      1. Try tr.wiktionary.org (==Rusça== section → [[wikilinks]])
      2. If empty, fall back to en.wiktionary.org two-hop:
           Russian → English glosses → {{t|tr|...}} templates
      3. Cache final result in eval/data/wikt_cache.db
    """

    def __init__(self, db_path: Path = CACHE_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        _init_db(self._conn)
        self._argos = None  # lazy-initialised on first Argos fallback

    def _get_argos(self):
        """Return a cached ArgosTranslator(ru→tr), initialising it on first call."""
        if self._argos is None:
            from eval.argos_translator import ArgosTranslator
            self._argos = ArgosTranslator("ru", "tr")
        return self._argos

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        """
        Return Turkish word(s) for a Russian lemma.

        *grammar* is the backend grammar string (e.g. "pronoun, personal, nominative").
        It guides POS-aware definition selection so e.g. "я" returns pronoun
        definitions ("ben") rather than letter-of-alphabet definitions.
        Returns [] if the word is missing or lookup fails.
        """
        key = _normalise(lemma)
        if not key:
            return []

        pos = _coarse_pos(grammar)
        # Include POS in the cache key so the same lemma in different POS
        # can have different cached results (e.g. "я" as pronoun vs. bare)
        cache_key = f"{key}|{pos}" if pos else key

        row = self._conn.execute(
            "SELECT tr_words FROM cache WHERE word = ?", (cache_key,)
        ).fetchone()
        if row is not None:
            return json.loads(row[0])

        # Not cached — try tr.wiktionary.org first, then en.wiktionary.org fallback
        tr_words: list[str] = []
        try:
            tr_words = _fetch_from_tr_wikt(key, preferred_pos=pos)
            time.sleep(2.0)
        except Exception:
            pass

        if not tr_words:
            # Fallback: en.wiktionary.org two-hop (Russian → English → Turkish)
            try:
                tr_words = _fetch_via_en_wikt(key, preferred_pos=pos)
            except Exception:
                pass

        if not tr_words:
            # Final fallback: Argos offline word translation (ru→tr)
            try:
                raw = self._get_argos().translate(key)
                if raw and raw.strip() and raw.strip().lower() != key.lower():
                    tr_words = [raw.strip()]
            except Exception:
                pass

        # Only cache non-empty results — empty results may be due to rate-limiting
        # and should be retried on the next run rather than poisoning the cache.
        if tr_words:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (word, tr_words, fetched_at) VALUES (?,?,?)",
                (cache_key, json.dumps(tr_words), int(time.time())),
            )
            self._conn.commit()
        return tr_words

    def close(self) -> None:
        self._conn.close()

