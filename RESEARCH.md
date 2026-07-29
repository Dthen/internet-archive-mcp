# Internet Archive APIs — Research Findings

Research for building a full-featured Internet Archive MCP server.
All endpoints below were **tested live with curl** on 2026-07-28. Examples are real responses.

---

## 0. TL;DR — Key Findings

- **No auth required** for all read operations (search, metadata, CDX, availability, thumbnails, content fetch).
- **Save Page Now (SPN2) DOES require auth** — anonymous POST returns `401 {"message":"You need to be logged in to use Save Page Now."}`. Needs S3-like access/secret keys from `archive.org/account/s3.php`.
- **CDX `matchType=domain` requires authorization** (returns `403 "This type of CDX query requires authorization."`). `exact` and `prefix` work anonymously.
- **Metadata API returns `{}` (HTTP 200, 2 bytes) for nonexistent/dark items** — must handle as "not found".
- **A descriptive User-Agent header is officially required** for all automated requests (see §6).
- **Existing MCP servers are almost all Wayback-only.** Only one (lakshyamehta03) covers the broader IA APIs, and none do collections/mediatype browsing well. The full-collections angle is genuinely open.
- **Two search engines exist:** `advancedsearch.php` (paged, capped at 10,000 results) and the **Scraping API** `services/search/v1/scrape` (cursor-based, for deep paging). Both use the same Lucene-like query syntax.

---

## 1. Advanced Search API

**Endpoint:** `https://archive.org/advancedsearch.php`
**Docs:** https://archive.org/help/aboutsearch.htm · query language described on the advancedsearch.php page itself.

### Parameters

| Param | Description | Example |
|-------|-------------|---------|
| `q` | Lucene-like query (required) | `q=collection:(opensource) AND mediatype:(texts)` |
| `fl[]` | Fields to return (repeatable). Omit → defaults to `identifier` only | `fl[]=identifier&fl[]=title&fl[]=downloads` |
| `rows` | Results per page (max ~10000 total reachable) | `rows=50` |
| `page` | Page number (1-indexed). **Sorted paged results capped at 10,000th result** | `page=1` |
| `sort[]` | Sort field + direction (repeatable, up to 3) | `sort[]=downloads desc` |
| `output` | `json`, `xml`, `csv`, `rss` | `output=json` |
| `callback` | JSONP callback name | `callback=foo` |
| `save` | `yes` to save query | `save=yes` |

### Tested request/response

```
GET https://archive.org/advancedsearch.php?q=collection:(opensource)+AND+mediatype:(texts)&fl[]=identifier&fl[]=title&fl[]=mediatype&rows=3&page=1&output=json
```
```json
{
  "responseHeader": {
    "status": 0, "QTime": 182,
    "params": {"query":"collection:opensource AND mediatype:texts",
               "qin":"collection:(opensource) AND mediatype:(texts)",
               "fields":"identifier,title,mediatype","wt":"json","rows":3,"start":0}
  },
  "response": {
    "numFound": 1241262, "start": 0,
    "docs": [
      {"identifier":"aitno-dai-v-1-10","mediatype":"texts","title":"bundle 5"},
      {"identifier":"anythingtext","mediatype":"texts","title":"abdelmonaimanyname"},
      {"identifier":"PSPXB1","mediatype":"texts","title":"PSPXB1"}
    ]
  }
}
```

### Available `fl[]` fields (tested — all returned data)

`identifier`, `title`, `mediatype`, `collection`, `creator`, `subject`, `language`,
`downloads`, `month`, `week`, `publicdate`, `year`, `stars`, `reviews_count`,
`avg_rating`, `item_size`, `format`, `description`, `date`, `addeddate`, `uploader`.

Sample enriched doc (single item):
```json
{"avg_rating":4.46, "collection":["cdl","yrlsc","iacl","americana","fav-..."],
 "downloads":..., "identifier":"goodytwoshoes00newyiala", "title":"...",
 "subject":[...], "creator":"...", "publicdate":"2007-...", "year":"1888"}
```
Note: `collection` and `subject` are **arrays** (an item can be in many collections, esp. `fav-*` user favorites).

### Sort fields (tested working)

`downloads desc`, `publicdate desc`, `titleSorter asc`, `avg_rating desc`, `createdate desc`, `week desc`, `month desc`.
Direction is `asc`/`desc`. Up to 3 `sort[]` allowed.

### Lucene-like query syntax

- Fielded: `field:value`, grouping `field:(a b)`.
- Boolean: `AND`, `OR`, `NOT` (NOT requires at least one positive term — `NOT test` alone fails).
- Free text: `q=test` searches default fields.
- **Range queries:** `downloads:[1000 TO 2000]`.
- Combine: `mediatype:(movies) AND collection:(opensource_movies)`.
- Wildcards / phrases supported per the on-page docs.

### Pagination limit (important)

> "We limit the number of sorted paged results returnable to 10,000. Paged sorted results are supported only until the 10,000th result." — aboutsearch.htm

For deep paging use the **Scraping API** (§1b).

### 1b. Scraping API (cursor-based deep paging)

**Endpoint:** `https://archive.org/services/search/v1/scrape`
Same query language. Parameters: `q`, `fields` (comma-delimited), `sorts` (comma-delimited; `identifier` must be last if present), `count` (min 100), `cursor`, `total_only`.

Tested:
```
GET https://archive.org/services/search/v1/scrape?fields=title,identifier&q=collection:nasa&count=100
```
```json
{"items":[{"title":"International Space Station exhibit","identifier":"00-042-154"}, ...],
 "total":208826, "count":100,
 "cursor":"W3siaWRlbnRpZmllclNvcnRlciI6IjAxLTEyLTE5X1NwYWNlLXRvLUdyb3VuZHMuemlwIn1d"}
```
Loop by passing the returned `cursor` back until it's absent. `total` with a cursor = remaining from cursor point.

---

## 2. Metadata API

**Endpoint:** `https://archive.org/metadata/{identifier}`
**Auth:** none.

### Response shape (tested on a real item)

Top-level keys:
```
created, d1, d2, dir, files, files_count, item_last_updated, item_size,
metadata, server, uniq, workable_servers
```
- `metadata` keys (varies by item): `identifier, mediatype, collection, creator, date, description, language, scanner, subject, title, uploader, publicdate, addeddate, access-restricted-item, curation`.
- `files`: array (one item had **188 files**). Each file:
  `name, source, mtime, size, md5, crc32, sha1, format, length, width, height, private`.
- `files_count`, `item_size` (bytes), `server`/`d1`/`d2`/`workable_servers` (download hosts), `dir` (item dir path).

### Reviews

`reviews` is present **only when the item has reviews** — then it's a list:
```json
[{"reviewbody":"...","reviewtitle":"Typical Turn of the Century story.",
  "reviewer":"TallpailofH20","reviewdate":"2007-04-21 04:10:46",
  "createdate":"2007-04-21 04:10:46","stars":"4"}, ...]
```
When there are no reviews, the key is **absent / null** (not an empty list). Handle both.

### Critical pitfall

For a nonexistent or dark identifier the API returns HTTP **200 with body `{}`** (2 bytes). You MUST treat empty-object as "not found" — do not assume keys exist.

### Collections are items too

A collection identifier (e.g. `prelinger`) resolves via the same endpoint with `metadata.mediatype == "collection"` and a `title`/`description`. Count items with search: `q=collection:prelinger` → `numFound: 10376`.

---

## 3. Wayback CDX API

**Endpoint:** `https://web.archive.org/cdx/search/cdx`
**Docs:** https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server

### Parameters

| Param | Description | Notes |
|-------|-------------|-------|
| `url` | Target URL. Add `*` for prefix | `url=example.com`, `url=example.com/*` |
| `matchType` | `exact` (default), `prefix`, `host`, `domain` | **`domain` requires auth (403 anon)**; `host`/`prefix`/`exact` work anon |
| `output` | `json` or `text` | json gives header row + rows |
| `fl` | Comma fields to return | `fl=timestamp,original,statuscode` |
| `limit` | Max results | `limit=5` |
| `from` / `to` | Timestamp range (YYYY or full) | `from=2020&to=2021` |
| `filter` | Filter on field, repeatable | `filter=statuscode:200`, `filter=mimetype:text/html`, negation `filter=!statuscode:200` |
| `collapse` | Dedup consecutive on field | `collapse=digest`, `collapse=urlkey` |
| `page` | Result page (with `limit`) | pairs with `showResumeKey` |
| `showResumeKey` | `true` → appends resume key row for paging | see below |
| `showNumCaptures` | include capture counts | **Intentionally omitted** from `wayback_snapshots` — adds a capture-count column with low utility for the MCP use case (agents typically need timestamps/URLs, not aggregate counts). Can be added later if needed. |
| `newest` / `oldest` | `newest=true` worked; `oldest=true` **timed out** in testing — avoid | |
| `gzip` | gzip output | |
| `fastLatest` | faster "latest" resolution | |

### Default JSON columns
`urlkey, timestamp, original, mimetype, statuscode, digest, length`

### Tested examples

Basic:
```
GET .../cdx?url=example.com&output=json&limit=5
[["urlkey","timestamp","original","mimetype","statuscode","digest","length"],
 ["com,example)/","20020120142510","http://example.com:80/","text/html","200","HT2D...","1792"], ...]
```
Custom fields:
```
GET .../cdx?url=example.com&matchType=exact&limit=3&output=json&fl=timestamp,original,statuscode
[["timestamp","original","statuscode"],
 ["20020120142510","http://example.com:80/","200"], ...]
```
Date range + revisit records appear with `mimetype=warc/revisit`, `statuscode=-`:
```
GET .../cdx?url=example.com&from=2020&to=2021&limit=3&output=json
```
Filter + collapse (anon OK for prefix/exact):
```
GET .../cdx?url=example.com&collapse=digest&limit=5&output=json
GET .../cdx?url=example.com&filter=mimetype:text/html&limit=3&output=json
```

### Pagination (page + resume key)

```
GET .../cdx?url=example.com&limit=2&page=1&output=json&showResumeKey=true
[["urlkey",...],
 ["com,example)/","20070202030042",...],
 ["com,example)/","20070210031936",...],
 [],                                   <-- separator
 ["eJxLzs_VSa1IzC3ISdXUVzAyMDA3MDI0MDA2tDQ2AwB_XgeS"]]   <-- resume key
```
Pass the resume key as `&resumeKey=...` (or increment `page`) to continue.

### Auth-required cases
- `matchType=domain` → `403 Forbidden: "This type of CDX query requires authorization."`
- Auth = S3-like keys via `Authorization: LOW <access>:<secret>` header (same keys as SPN2).

---

## 4. Save Page Now (SPN2)

**Endpoint:** `https://web.archive.org/save/{url}` (POST) · API: `https://web.archive.org/save/`
**Docs:** SPN2 API doc (Google Doc linked from Mearman repo).

### Tested — anonymous is rejected
```
POST https://web.archive.org/save/https://example.com   (Accept: application/json)
HTTP 401
{"message":"You need to be logged in to use Save Page Now."}
```

### Auth requirements
- Requires an archive.org account + S3-like API keys from `https://archive.org/account/s3.php`.
- Send `Authorization: LOW <access_key>:<secret_key>`.
- Authenticated users get **higher SPN2 rate limits** (anonymous either blocked or heavily throttled).
- Typical flow: POST to start save → returns a job id / status URL → poll `https://web.archive.org/save/status/{job}` for completion.

**Design implication:** `save_page` tool must accept optional access/secret keys (env vars). Without keys it should fail gracefully with a clear message.

---

## 5. Other Endpoints

### 5a. Item thumbnail / image
**Endpoint:** `https://archive.org/services/img/{identifier}`
- Returns the item's thumbnail image directly (JPEG/PNG). Tested: real item → `200 image/jpeg 26418 bytes`.
- For items with no image it **302-redirects to `https://archive.org/images/notfound.png`** (a 2.2KB PNG). Follow redirects (`-L`).
- Alt pattern: `https://archive.org/download/{identifier}/__ia_thumb.jpg` also works (same bytes).

### 5b. Availability API (Wayback)
**Endpoint:** `https://archive.org/wayback/available?url={url}` — no auth.
```json
{"url":"example.com",
 "archived_snapshots":{"closest":{"status":"200","available":true,
   "url":"http://web.archive.org/web/20260728023543/https://example.com/",
   "timestamp":"20260728023543"}}}
```
Great for a quick "is there a snapshot?" check without CDX.

### 5c. Fetch archived page content
- Human/iframe URL: `https://web.archive.org/web/{timestamp}/{url}` → **302** to resolved snapshot (e.g. `.../web/20210101000012/http://example.com/`).
- **Raw/original bytes:** append `id_` to the timestamp: `https://web.archive.org/web/2020id_/https://example.com/` → returns the original archived HTML (tested: `200 text/html`, no Wayback toolbar injection). **Use `id_` for content extraction.**
- Timestamp can be partial (`2020`) → resolves to nearest.

### 5d. Download / file access
- Item file listing page: `https://archive.org/download/{identifier}/` (HTML index).
- Direct file: `https://archive.org/download/{identifier}/{filename}`.

### 5e. Reviews
No separate reviews endpoint needed — reviews are embedded in the Metadata API response (§2).

### 5f. Related items / recommendations
- `https://archive.org/recommendations/{identifier}` — endpoint exists but returned nothing useful in quick testing; not well documented. Lower priority.

---

## 6. Rate Limits & Politeness

**Official doc:** https://archive.org/developers/bots.html ("Bots, LLMs, and Automated Access").

### Hard requirements
1. **User-Agent is mandatory.** All automated requests must include a descriptive UA identifying tool name, version, and (for AI agents) the model. Example:
   `User-Agent: internet-archive-mcp/1.0.0 (claude-sonnet-4-20250514)`
   → Our MCP server MUST set this on every request.
2. **Honor `429 Too Many Requests` and `Retry-After` headers.**

### Guidance (no hard published numeric limit for reads)
- Add delays between bulk requests.
- Cache responses; avoid repeat requests for the same data.
- Prefer bulk/batch endpoints over many individual calls.
- Retries with exponential backoff.
- Limit concurrency (their example: `parallel -j4 --delay 1`).

### Practical
- Read APIs (search/metadata/CDX) are generous but will throttle aggressive scraping. A modest in-server rate limiter + cache (like Mearman's server) is the right design.
- SPN2 has its own stricter per-user rate limits (higher when authenticated).

---

## 7. Existing MCP Servers (avoid duplication)

| Repo | Lang | Coverage | Stars | Notes |
|------|------|----------|-------|-------|
| **Mearman/mcp-wayback-machine** | TypeScript | Wayback only: `save_url` (SPN2), `get_archived_url`, `search_archives` (CDX), `check_archive_status` (sparkline), `list_screenshots`, `compare_snapshots`, `clear_cache` | 40 | Most polished. Has rate limiter + cache + optional auth. **Wayback-only — no IA collections/search/metadata.** |
| **sisilet/wayback-mcp** | Python | `get_snapshots` (CDX), `get_archived_page`, `search_items` (advancedsearch) + a `wayback://` resource | 4 | Adds basic item search but no metadata/files/collections. |
| **fuushyn/wayback-machine-mcp** | — | Snapshot fetching | small | Wayback-only. |
| **lakshyamehta03/wayback-machine-mcp** (Glama) | Python (async) | "six core APIs — Availability, CDX, Advanced Search, Metadata, and Wayback content" | — | **Closest to full-featured**, but listing suggests no collection browsing / mediatype tooling / thumbnails / scraping-API deep paging. |
| cyreslab-ai-wayback-mcp-server (LobeHub) | — | Wayback list/fetch/search | — | Wayback-focused. |

### Gap analysis — what's NOT covered anywhere
- **Collection browsing & mediatype filtering** as first-class tools.
- **Full item metadata + file listing** (only lakshyamehta03 touches metadata).
- **Reviews** extraction.
- **Thumbnails** (`services/img`).
- **Scraping API** cursor deep-paging.
- **Availability API** as a lightweight tool.
- A single server combining **both** full IA collections AND full Wayback. Mearman is Wayback-only; the IA-side servers are thin.

→ A server that does **collections + search + metadata/files/reviews + thumbnails + Wayback CDX + availability + content fetch + (auth-gated) save** is genuinely differentiated.

---

## 8. Collections & Mediatypes

### Mediatypes (live counts via `q=mediatype:X`, 2026-07-28)

| mediatype | numFound |
|-----------|----------|
| `texts` | 51,580,328 |
| `web` | 23,732,557 |
| `movies` | 16,684,675 |
| `audio` | 13,869,969 |
| `data` | 7,296,337 |
| `image` | 5,682,347 |
| `collection` | 3,582,890 |
| `software` | 1,432,867 |

Filter with `q=mediatype:(texts)` (or combine: `mediatype:(movies) AND collection:(prelinger)`).

### Collections
- A collection is itself an item with `mediatype: collection`. Browse via `q=collection:{identifier}`.
- Well-known: `prelinger` (Prelinger Archives films, ~10,376 items), `nasa` (~208,826), `opensource`, `americana`, `cdl` (controlled digital lending), `fav-*` (user favorites).
- Community upload collections (from bots.html): `opensource` (texts), `opensource_movies` (video), `opensource_audio` (audio), `opensource_image` (images), `opensource_media` (data), `open_source_software` (software). `test_collection` = sandbox (auto-purged ~30 days).
- There's no single "list all collections" API; discover via search `q=mediatype:collection` (3.5M rows — must page/scrape) or known identifiers.

---

## 9. MCP Server Design Considerations

### Recommended tool set (prioritized)

**Tier 1 — core, high value, no auth:**
1. `search_archive(query, mediatype?, collection?, fields?, sort?, rows, page)` — advancedsearch.php. Default `fl` to a sensible set (identifier, title, mediatype, creator, downloads, publicdate). Return `numFound` for pagination awareness.
2. `get_item_metadata(identifier, include_files?)` — metadata API. **Default to metadata-only; make files opt-in** (files arrays can be huge — 188+ entries). Handle `{}` → "not found".
3. `list_item_files(identifier, format_filter?)` — files from metadata, optionally filtered by `format`.
4. `wayback_snapshots(url, matchType?, from?, to?, limit, filter?, collapse?)` — CDX. Default `matchType=exact`, default `limit` small (e.g. 25). Expose `collapse=digest` option to dedup.
5. `wayback_fetch(url, timestamp?, raw?)` — fetch archived content. Use `id_` suffix for raw original bytes; strip/return text.
6. `wayback_availability(url)` — availability API, cheap "nearest snapshot" lookup.

**Tier 2 — value-add, differentiators:**
7. `browse_collection(collection, rows, page)` — search `q=collection:X`.
8. `get_collection_info(identifier)` — metadata for a collection item.
9. `get_item_reviews(identifier)` — reviews from metadata.
10. `get_item_thumbnail(identifier)` — `services/img` (return URL or base64; follow redirect → notfound.png).
11. `search_archive_deep(query, fields, cursor)` — scraping API for >10k paging.

**Tier 3 — auth-gated:**
12. `save_page(url, access_key?, secret_key?)` — SPN2. Requires keys (env). Fail gracefully with guidance if absent.

### Response size concerns (critical for MCP)
- **Metadata `files` can be enormous** (hundreds of entries, each with hashes). Never dump full metadata blindly — summarize or paginate files.
- **CDX for popular domains is unbounded** (example.com has captures since 2002; big domains → millions). Always enforce a default `limit` and recommend `collapse`/`filter`.
- **`collection` field in search docs includes dozens of `fav-*` entries** — consider trimming `fav-*` from returned collections to reduce noise.
- **Archived page HTML can be large** — for `wayback_fetch`, offer a text-extraction / char-cap option.
- Search `description`/`subject` fields can be long — make fields explicit rather than dumping everything.

### Pagination strategy
- Search: expose `page` + return `numFound` and a computed `total_pages`. Warn when approaching the 10,000 sorted cap; offer the scraping-API tool beyond that.
- CDX: expose `limit` + `page`, and surface the resume key when `showResumeKey` used. Default to small limits.

### Cross-cutting
- **Set a descriptive User-Agent on every request** (mandatory per IA). e.g. `internet-archive-mcp/<ver> (<model>)`.
- **In-server rate limiter + response cache** (mirror Mearman's approach) — protects against 429 and repeat calls.
- **Honor 429 + Retry-After**, exponential backoff.
- Treat metadata `{}` and CDX empty results as explicit "not found" tool outputs.
- **Language choice:** existing ecosystem splits TS (Mearman, polished) vs Python (sisilet, lakshyamehta03). Python has the official `internetarchive` library and `ia` CLI for reference; TS has the most-starred MCP. Either works — pick per the parent project's stack preference.

### Reference links
- Developer portal: https://archive.org/developers/
- APIs index: https://archive.org/developers/index-apis.html
- Search help: https://archive.org/help/aboutsearch.htm
- Bots/LLM policy: https://archive.org/developers/bots.html
- CDX server docs: https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server
- SPN2 API doc: (Google Doc, linked from Mearman/mcp-wayback-machine README)
- Official IA skills for AI: https://github.com/internetarchive/internet-archive-skills
- `ia` CLI / Python lib: https://pypi.org/project/internetarchive/
