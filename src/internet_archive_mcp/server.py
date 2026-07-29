"""FastMCP server for the Internet Archive."""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

from internet_archive_mcp.client import ArchiveClient

mcp = FastMCP(
    "internet-archive",
    instructions=(
        "Search the Internet Archive, browse collections, get item metadata "
        "and files, read reviews, and access the Wayback Machine. "
        "All read operations are anonymous; Save Page Now requires API keys."
    ),
)

_client = ArchiveClient()


# ---------------------------------------------------------------------------
# Tier 1 — Core tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_archive(
    query: str,
    mediatype: str | None = None,
    collection: str | None = None,
    fields: list[str] | None = None,
    sort: list[str] | None = None,
    rows: int = 20,
    page: int = 1,
) -> dict | str:
    """Search the Internet Archive catalog using the advanced search API.

    Returns paginated results with item identifiers, titles, creators, and
    download counts. Supports filtering by media type and collection.

    Args:
        query: Free-text Lucene query (e.g. "jazz piano", "creator:ellis").
        mediatype: Filter by media type (texts, web, movies, audio, data, image, collection, software).
        collection: Restrict results to a specific collection identifier.
        fields: Metadata fields to return per result. Defaults to identifier,
            title, mediatype, creator, downloads, publicdate, year.
        sort: Up to 3 sort expressions (e.g. ["downloads desc", "year asc"]).
        rows: Number of results per page (default 20).
        page: Page number, 1-indexed (default 1).
    """
    try:
        return await _client.search_archive(
            query,
            mediatype=mediatype,
            collection=collection,
            fields=fields,
            sort=sort,
            rows=rows,
            page=page,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_item_metadata(
    identifier: str,
    include_files: bool = False,
) -> dict | str:
    """Get full metadata for an Internet Archive item.

    Returns the item's metadata block (title, creator, description, subject,
    etc.), server info, and optionally the file listing.

    Args:
        identifier: The unique item identifier (e.g. "nightofthelivingdead").
        include_files: If True, include the files array and files_count.
    """
    try:
        return await _client.get_item_metadata(
            identifier, include_files=include_files
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def list_item_files(
    identifier: str,
    format_filter: str | None = None,
) -> list[dict] | str:
    """List all files belonging to an Internet Archive item.

    Each file entry includes name, format, size, and other technical metadata.
    Optionally filter by format (case-insensitive).

    Args:
        identifier: The unique item identifier.
        format_filter: If provided, only return files whose format matches
            exactly (case-insensitive, e.g. "VBR MP3", "Text PDF").
    """
    try:
        return await _client.list_item_files(
            identifier, format_filter=format_filter
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_item_reviews(identifier: str) -> list[dict] | str:
    """Get user reviews for an Internet Archive item.

    Returns a list of review objects with reviewer, stars, title, and body.
    Returns an empty list if the item has no reviews.

    Args:
        identifier: The unique item identifier.
    """
    try:
        return await _client.get_item_reviews(identifier)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def wayback_snapshots(
    url: str,
    match_type: str = "exact",
    from_year: int | None = None,
    to_year: int | None = None,
    limit: int = 25,
    filter_expr: str | list[str] | None = None,
    collapse: str | None = None,
    fields: list[str] | None = None,
    page: int | None = None,
    show_resume_key: bool = False,
    resume_key: str | None = None,
    newest: bool = False,
    fast_latest: bool = False,
) -> list[dict] | dict | str:
    """Search the Wayback Machine CDX index for snapshots of a URL.

    Returns a list of snapshot records with timestamp, original URL, MIME
    type, status code, and digest. Use match_type to control URL matching.

    When show_resume_key is True, returns a dict with "snapshots" and
    "resume_key" keys for paging through large result sets.

    Args:
        url: The URL to search for (e.g. "example.com/page").
        match_type: One of "exact", "prefix", "host", or "domain".
            Note: "domain" requires authentication and will return an error.
        from_year: Only include snapshots from this year onward.
        to_year: Only include snapshots up to this year.
        limit: Maximum number of results (default 25).
        filter_expr: CDX filter expression(s), e.g. "statuscode:200" or
            ["mimetype:text/html", "statuscode:200"].
        collapse: Collapse results by a field, e.g. "urlkey" or
            "timestamp:4" (group by year).
        fields: CDX fields to return (e.g. ["timestamp", "original",
            "statuscode"]). Defaults to all fields.
        page: Result page number (pairs with limit for pagination).
        show_resume_key: When True, response includes a resume key for
            continuing pagination. Returns a dict instead of a list.
        resume_key: Pass a resume key from a previous response to continue
            from where it left off.
        newest: When True, return the newest snapshot first.
        fast_latest: When True, use a faster algorithm for latest snapshots.
    """
    try:
        return await _client.wayback_snapshots(
            url,
            match_type=match_type,
            from_year=from_year,
            to_year=to_year,
            limit=limit,
            filter_expr=filter_expr,
            collapse=collapse,
            fields=fields,
            page=page,
            show_resume_key=show_resume_key,
            resume_key=resume_key,
            newest=newest,
            fast_latest=fast_latest,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def wayback_availability(url: str) -> dict | str:
    """Check if a URL has an archived snapshot in the Wayback Machine.

    Quick single-snapshot check — returns the closest available snapshot
    timestamp and URL. Good for "is this archived?" questions.

    Args:
        url: The URL to check (e.g. "https://example.com").
    """
    try:
        return await _client.wayback_availability(url)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def wayback_fetch(
    url: str,
    timestamp: str | None = None,
    raw: bool = True,
    char_limit: int = 50000,
) -> dict | str:
    """Fetch archived page content from the Wayback Machine.

    Retrieves the actual HTML/text content of an archived page. By default
    fetches raw content (no Wayback toolbar injection). Content is truncated
    to char_limit characters.

    Args:
        url: The original URL to fetch from the archive.
        timestamp: Wayback timestamp (e.g. "20200101120000"). If omitted,
            fetches the most recent snapshot.
        raw: If True (default), fetch raw content without the Wayback
            Machine toolbar. Appends "id_" to the timestamp.
        char_limit: Maximum characters to return (default 50000). Content
            beyond this limit is truncated.
    """
    try:
        return await _client.wayback_fetch(
            url,
            timestamp=timestamp,
            raw=raw,
            char_limit=char_limit,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Tier 2 — Differentiator tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def browse_collection(
    collection: str,
    rows: int = 20,
    page: int = 1,
    sort: list[str] | None = None,
) -> dict | str:
    """Browse items within a specific Internet Archive collection.

    Returns paginated results sorted by downloads (descending) by default.
    Use this to explore what's in a collection like "opensource_audio".

    Args:
        collection: The collection identifier (e.g. "librivoxaudio").
        rows: Number of results per page (default 20).
        page: Page number, 1-indexed (default 1).
        sort: Sort expressions. Defaults to ["downloads desc"].
    """
    try:
        return await _client.browse_collection(
            collection, rows=rows, page=page, sort=sort
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_collection_info(identifier: str) -> dict | str:
    """Get metadata about an Internet Archive collection.

    Returns the collection's metadata including title, description, and
    item count. Adds a note if the identifier is not actually a collection.

    Args:
        identifier: The collection identifier (e.g. "opensource_audio").
    """
    try:
        return await _client.get_collection_info(identifier)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
def get_item_thumbnail(identifier: str) -> str:
    """Get the thumbnail image URL for an Internet Archive item.

    Returns a URL that redirects to the item's thumbnail image. Can be
    used in markdown image syntax or passed to image tools.

    Args:
        identifier: The unique item identifier.
    """
    try:
        return _client.get_item_thumbnail_url(identifier)
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
async def search_archive_deep(
    query: str,
    fields: list[str] | None = None,
    sorts: list[str] | None = None,
    count: int = 100,
    cursor: str | None = None,
    total_only: bool = False,
) -> dict | str:
    """Deep search using the Internet Archive scraping API with cursor paging.

    Unlike search_archive (limited to 1000 pages), this supports unlimited
    pagination via cursors. Minimum count is 100 per request.

    Args:
        query: Lucene search query string.
        fields: Comma-separated metadata fields to return.
        sorts: Sort expressions (e.g. ["downloads desc"]).
        count: Results per page, minimum 100 (default 100).
        cursor: Opaque cursor string from a previous response for the
            next page. Omit for the first page.
        total_only: When True, return only the total count without item
            records. Useful for quick cardinality checks.
    """
    try:
        return await _client.search_archive_deep(
            query, fields=fields, sorts=sorts, count=count, cursor=cursor,
            total_only=total_only,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Tier 3 — Auth-gated tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def save_page(
    url: str,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> dict | str:
    """Save a URL to the Wayback Machine using Save Page Now (SPN2).

    Requires Internet Archive S3 API keys. Keys can be provided as arguments
    or set as environment variables IA_ACCESS_KEY and IA_SECRET_KEY.
    Get keys at https://archive.org/account/s3.php.

    Args:
        url: The URL to archive.
        access_key: IA S3 access key. Falls back to IA_ACCESS_KEY env var.
        secret_key: IA S3 secret key. Falls back to IA_SECRET_KEY env var.
    """
    ak = access_key or os.environ.get("IA_ACCESS_KEY")
    sk = secret_key or os.environ.get("IA_SECRET_KEY")
    try:
        return await _client.save_page(url, access_key=ak, secret_key=sk)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def save_page_status(job_id: str) -> dict | str:
    """Check the status of a Save Page Now (SPN2) job.

    After calling save_page, use the returned job_id to poll for
    completion. Returns status information including whether the save
    has completed and the resulting Wayback URL.

    Args:
        job_id: The job ID returned by save_page.
    """
    try:
        return await _client.save_page_status(job_id)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
