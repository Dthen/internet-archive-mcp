# Internet Archive MCP Server

Full-coverage Internet Archive MCP server — search, metadata, collections, and Wayback Machine in one server.

## Why this server?

Existing Internet Archive MCP servers are almost all **Wayback-only** — small 3–4 tool wrappers around the CDX snapshot API. This is the only server that combines:

- **Full IA collections access** — search, metadata, files, reviews, and collection browsing across all media types (texts, audio, movies, software, images)
- **Full Wayback Machine coverage** — CDX snapshot search, availability checks, and raw content fetch
- **13 tools** organized in three tiers (core, differentiator, auth-gated)
- **Anonymous reads** — every read operation works with zero configuration
- **Auth-gated writes** — Save Page Now requires IA S3 keys, cleanly separated from the read path

## Quick start

### Install

```bash
cd internet-archive-mcp
pip install -e .
```

### Hermes Agent

Add to your Hermes `config.yaml`:

```yaml
mcp_servers:
  internet-archive:
    command: python3
    args: ['-m', 'internet_archive_mcp.server']
    env:
      PYTHONPATH: /path/to/internet-archive-mcp/src
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "internet-archive": {
      "command": "python3",
      "args": ["-m", "internet_archive_mcp.server"],
      "env": {"PYTHONPATH": "/path/to/internet-archive-mcp/src"}
    }
  }
}
```

## Tool reference

### Tier 1 — Core tools

| Tool | Key args | Returns |
|------|----------|---------|
| `search_archive` | `query`, `mediatype?`, `collection?`, `fields?`, `sort?`, `rows=20`, `page=1` | Paginated results: `{numFound, start, docs, total_pages}` |
| `get_item_metadata` | `identifier`, `include_files=False` | Full metadata block (title, creator, description, subjects, server info) |
| `list_item_files` | `identifier`, `format_filter?` | List of file dicts (name, format, size, technical metadata) |
| `get_item_reviews` | `identifier` | List of review objects (reviewer, stars, title, body) |
| `wayback_snapshots` | `url`, `match_type="exact"`, `from_year?`, `to_year?`, `limit=25`, `filter_expr?`, `collapse?`, `fields?`, `page?`, `show_resume_key?`, `resume_key?`, `newest?`, `fast_latest?` | List of CDX snapshot records (timestamp, URL, MIME, status, digest) |
| `wayback_availability` | `url` | Closest available snapshot timestamp and URL |
| `wayback_fetch` | `url`, `timestamp?`, `raw=True`, `char_limit=50000` | Archived page content (raw by default, no toolbar) |

### Tier 2 — Differentiator tools

| Tool | Key args | Returns |
|------|----------|---------|
| `browse_collection` | `collection`, `rows=20`, `page=1`, `sort?` | Paginated items in a collection (sorted by downloads desc) |
| `get_collection_info` | `identifier` | Collection metadata with item count; notes if not a collection |
| `get_item_thumbnail` | `identifier` | Thumbnail image URL (usable in markdown) |
| `search_archive_deep` | `query`, `fields?`, `sorts?`, `count=100`, `cursor?`, `total_only?` | Cursor-paged results for unlimited deep pagination |

### Tier 3 — Auth-gated tools

| Tool | Key args | Returns |
|------|----------|---------|
| `save_page` | `url`, `access_key?`, `secret_key?` | SPN2 job status and archived URL |
| `save_page_status` | `job_id` | Status of a Save Page Now job (completion, Wayback URL) |

## Configuration

All read tools work anonymously with no configuration. The only tool requiring credentials is `save_page` (Save Page Now).

### Environment variables

| Variable | Purpose |
|----------|---------|
| `IA_ACCESS_KEY` | Internet Archive S3 access key |
| `IA_SECRET_KEY` | Internet Archive S3 secret key |

Get your keys at: **https://archive.org/account/s3.php** (free account required).

Keys can also be passed directly as tool arguments (`access_key`, `secret_key`) — env vars are the fallback.

## Rate limiting & caching

The client is designed to be a polite, well-behaved consumer of IA APIs:

- **Rate limiter** — minimum 0.5s interval between all outgoing requests
- **Bounded TTL cache** — in-memory cache with 256-entry cap; search and metadata results cached for 1 hour; oldest entries evicted first
- **429 retry with backoff** — automatic retry on HTTP 429 with exponential backoff (honors `Retry-After` header), up to 3 attempts
- **Mandatory User-Agent** — every request carries a descriptive `User-Agent: internet-archive-mcp/0.1.0 (...)` header per IA policy

## Development

```bash
# Install with dev dependencies
pip install -e '.[dev]'

# Run tests
pytest tests/ -v
```

## Architecture

```
internet-archive-mcp/
├── src/internet_archive_mcp/
│   ├── __init__.py
│   ├── client.py      # Async httpx client — all API logic
│   └── server.py      # FastMCP tool definitions (thin wrappers)
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_client.py
│   ├── test_qa_fixes.py
│   ├── test_qa_round2.py
│   ├── test_server.py
│   └── test_tools.py
├── RESEARCH.md        # API research findings (tested with curl)
└── pyproject.toml
```

- **`client.py`** — `ArchiveClient` class using async `httpx`. Talks to two base URLs: `https://archive.org` (search, metadata, availability) and `https://web.archive.org` (CDX, content fetch, Save Page Now). Owns caching, rate limiting, and retry logic.
- **`server.py`** — FastMCP server with 13 `@mcp.tool()` definitions. Each tool is a thin async wrapper that delegates to `ArchiveClient` and converts exceptions to error strings.

## Design decisions

The Related Items API (`archive.org/recommendations/{id}`) was investigated during research but returned no useful data. It is intentionally excluded. See RESEARCH.md §5f.

## Links

- [RESEARCH.md](RESEARCH.md) — full API research with tested curl examples
- [Internet Archive Developer Docs](https://archive.org/developers)
- [IA Bots Policy](https://archive.org/developers/bots.html) — terms of service and acceptable use for automated access
