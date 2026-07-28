# Internet Archive MCP Server

MCP server wrapping the [Internet Archive](https://archive.org) APIs — full-featured access to search, metadata, collections, Wayback Machine CDX, and Save Page Now.

## Why

- 800B+ archived web pages, plus books, audio, video, software, images
- Existing MCP coverage is **Wayback-only** (3-4 small servers that just do snapshot lookup). Nobody has built a full-featured one covering the complete collections
- Multiple free endpoints, no auth required

## API Notes

- **Auth:** None for read operations. Free.
- **Advanced Search:** `https://archive.org/advancedsearch.php?q=...&output=json` — search all metadata across all collections
- **Metadata:** `https://archive.org/metadata/{identifier}` — full item details (files, reviews, metadata)
- **Wayback CDX:** `https://web.archive.org/cdx/search/cdx?url=...&output=json` — snapshot history for any URL
- **Save Page Now:** Programmatically archive a URL
- **Rate limits:** "Be polite" — no hard published limit

## Rough Tool Ideas

- `search_archive(query, mediatype?, collection?)` — search across all IA collections
- `get_item_metadata(identifier)` — full details for an item
- `list_item_files(identifier)` — files available for an item
- `wayback_snapshots(url)` — snapshot history for a URL via CDX
- `wayback_fetch(url, timestamp?)` — retrieve an archived page
- `save_page(url)` — archive a URL via Save Page Now
- `browse_collection(collection)` — list items in a collection

## Status

⚠️ **Before proceeding:** This needs proper research and planning before any code is written. Use the `plan` skill for a thorough execution plan and `subagent-driven-development` for implementation. Research first, build second.

### Research TODO
- [ ] Map all API endpoints and their parameters/response shapes
- [ ] Test Advanced Search query language (Lucene-style syntax)
- [ ] Understand CDX API pagination and filtering options
- [ ] Check Save Page Now rate limits and auth requirements
- [ ] Investigate collection-specific search (books, audio, video)
- [ ] Survey existing Wayback MCPs to avoid duplication
- [ ] Decide: TypeScript or Python?
