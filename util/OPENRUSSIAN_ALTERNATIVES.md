# OpenRussian Integration Alternatives

## Current Status (as of May 2026)
- **GitHub CSV Mirrors**: ❌ All inaccessible (404 errors)
  - `openrussian/russian-dictionary` main/master branches
  - `Badestrand/russian-dictionary` forks
- **Current Solution**: ✅ Wiktionary API fallback
  - En.wiktionary.org REST API for English definitions
  - Live lookups with User-Agent header
  - Per-word caching to minimize API calls

## Viable Integration Approaches

### 1. 🌐 OpenRussian.org Live Website
**Status**: ✅ Website is live (HTTP 200)
**Approach**: Web scraping or API discovery
- URL: https://openrussian.org/
- Could implement:
  - Selenium-based automated word lookups (slow, resource-intensive)
  - Direct API if documented (research needed)
  - Contact maintainers for data export options

**Implementation Effort**: ⭐⭐⭐⭐ (High - requires reverse engineering or API research)

---

### 2. 🔗 Archive.org / Wayback Machine
**Status**: ⚠️ Need to verify cached CSV snapshots
**Approach**: Download historical CSV snapshots from Archive.org
- URL: https://archive.org/wayback/available
- Query cached versions of GitHub CSV URLs
- Download and cache locally

**Implementation**:
```python
# Pseudo-code
def fetch_from_archive_org(url):
    archive_api = f"https://archive.org/wayback/available?url={url}"
    response = requests.get(archive_api)
    snapshot = response.json()['archived_snapshots']['closest']
    return requests.get(snapshot['url'])
```

**Implementation Effort**: ⭐⭐ (Low - straightforward HTTP fallback)
**Pros**: One-time download, reliable, fast after cache
**Cons**: Depends on Wayback Machine availability, data may be outdated

---

### 3. 📖 Russian Wiktionary (ru.wiktionary.org)
**Status**: ⚠️ API returns 501 (endpoint not available in that format)
**Approach**: Parse Russian Wiktionary page structure
- ru.wiktionary.org has Russian word definitions
- Could implement web scraper for ru.wiktionary.org
- Or use MediaWiki API to query Russian entries

**Implementation Effort**: ⭐⭐⭐ (Medium - needs parsing logic)
**Pros**: Native Russian definitions, comprehensive
**Cons**: Different API structure than English Wiktionary

---

### 4. 🐍 Academic & Open-Source Datasets
**Status**: ✅ Multiple projects exist
**Options**:
- **Russian National Corpus (RNC)**: openrussian.org-like coverage
- **UDPipe**: Morphological parses for Russian (we use spaCy, similar)
- **Moscow Dependency Parser**: Syntactic analysis
- **GCIDE with Russian additions**: Free dictionary databases

**Implementation Effort**: ⭐⭐⭐⭐ (High - requires integration with different formats)

---

### 5. 🏗️ Hybrid Multi-Source Strategy (RECOMMENDED)
Combine multiple reliable sources for better coverage:

```python
# Enhanced lookup chain:
lookup(word) → [
    1. Local cache (disk/database)
    2. Wiktionary API (en.wiktionary.org) - Current ✅
    3. Archive.org Historical CSV
    4. Russian Wiktionary (ru.wiktionary.org) via MediaWiki
    5. Lemma as fallback
]
```

**Implementation Effort**: ⭐⭐⭐ (Medium - incremental fallback additions)
**Current Code Location**: `backend/openrussian.py`

---

## Recommended Next Steps

### Priority 1: Archive.org Fallback (Quick Win)
- **Time**: ~30 minutes
- **Impact**: Adds historical data layer
- **Risk**: Low (non-breaking)
- Implementation:
  1. Try Archive.org API when CSV download fails
  2. Parse archived CSV snapshot
  3. Cache locally to `.cache/openrussian_words_archived.csv`

### Priority 2: Russian Wiktionary Integration
- **Time**: ~1-2 hours
- **Impact**: Native Russian definitions
- **Risk**: Medium (different data structure)
- Implementation:
  1. Research MediaWiki API for Russian Wiktionary
  2. Query `ru.wiktionary.org/w/api.php`
  3. Parse Russian definition structure
  4. Add as secondary fallback after English Wiktionary

### Priority 3: OpenRussian.org API Discovery
- **Time**: ~1 hour (research)
- **Impact**: High (official source)
- **Risk**: Unknown (depends on API availability)
- Implementation:
  1. Inspect openrussian.org/network tab for API calls
  2. Contact openrussian.org maintainers for API docs
  3. Implement if API exists

---

## Current Implementation Details

**File**: `backend/openrussian.py`

**Fallback Chain**:
1. CSV download from 3 GitHub mirrors → **All 404 ❌**
2. Wiktionary API (en.wiktionary.org) → **Working ✅**
3. Lemma as stub → **Final fallback ✅**

**Caching Strategy**:
- `_wiktionary_cache`: In-memory word cache (per-process)
- `.cache/openrussian_words.csv`: Persistent local cache (if downloaded)
- Per-word Wiktionary lookups: Minimal duplicate queries

**Configuration**:
```python
# Can be extended to support multiple sources:
DICT_SOURCES = {
    "github_csv": {...},         # Currently 404
    "wiktionary_en": {...},      # Currently active
    "wiktionary_ru": {...},      # To implement
    "archive_org": {...},        # To implement
}
```

---

## Testing Checklist

- [ ] Archive.org CSV availability confirmed
- [ ] Russian Wiktionary parsing tested
- [ ] Hybrid fallback chain validated
- [ ] Performance with multiple lookups benchmarked
- [ ] Cache invalidation strategy defined
- [ ] Error logging for fallback decisions

---

## Related Issues
- Original OpenRussian GitHub: https://github.com/openrussian/russian-dictionary
- Fork activity: Multiple forks exist but with same 404 issue
- Last known active update: ~2023

## Future: Direct Collaboration
Consider:
1. **Contributing to OpenRussian**: Offer to host/mirror CSV data
2. **Reaching out**: Email project maintainers for data export
3. **Creating mirror**: GitHub Actions to periodically backup/mirror data
