import json
import os
import urllib.parse
from typing import List, Dict, Any

import azure.functions as func
import httpx

# arXiv parsing needs feedparser (tiny, pure-python)
import feedparser

# -------- Helpers

def _cors_headers() -> Dict[str, str]:
    allowed = os.environ.get("CORS_ALLOWED_ORIGINS", "*")
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Max-Age": "86400",
    }

def _bad_request(msg: str) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": msg}),
        status_code=400,
        mimetype="application/json",
        headers=_cors_headers(),
    )

def _ok(payload: Any, extra_headers: Dict[str, str] | None = None) -> func.HttpResponse:
    headers = _cors_headers()
    if extra_headers:
        headers.update(extra_headers)
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
        headers=headers,
    )

# -------- Data sources

async def fetch_crossref(query: str, rows: int, offset: int) -> Dict[str, Any]:
    """
    Crossref: great coverage of journals, conferences, books (metadata-first).
    Docs: https://api.crossref.org/swagger-ui/index.html
    """
    params = {
        "query": query,
        "rows": rows,
        "offset": offset,
        # Prefer newer items when ties
        "sort": "published",
        "order": "desc",
    }
    url = "https://api.crossref.org/works"
    # Good practice: include a mailto in UA for polite rate-limiting
    headers = {
        "User-Agent": os.environ.get(
            "CROSSREF_UA",
            "copaco-research-fn/1.0 (mailto:denitsa.draganova@copaco.com)"
        )
    }

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

    items = data.get("message", {}).get("items", []) or []
    total = data.get("message", {}).get("total-results", 0)

    def first_or_none(x, key):
        v = x.get(key)
        return v[0] if isinstance(v, list) and v else v

    results: List[Dict[str, Any]] = []
    for it in items:
        authors = []
        for a in it.get("author", []) or []:
            given = a.get("given", "")
            family = a.get("family", "")
            full = " ".join([given, family]).strip() or a.get("name", "")
            if full:
                authors.append(full)

        date = it.get("published-print") or it.get("published-online") or it.get("issued") or {}
        year = None
        try:
            parts = date.get("date-parts", [])
            if parts and parts[0]:
                year = parts[0][0]
        except Exception:
            pass

        # Prefer publisher-provided link to a PDF if present
        pdf_url = None
        for l in it.get("link", []) or []:
            if l.get("content-type", "").lower() in ("application/pdf", "pdf"):
                pdf_url = l.get("URL")
                break

        results.append({
            "source": "crossref",
            "id": it.get("DOI") or it.get("URL"),
            "title": it.get("title", [""])[0] if it.get("title") else "",
            "authors": authors,
            "venue": first_or_none(it, "container-title"),
            "publisher": it.get("publisher"),
            "year": year,
            "doi": it.get("DOI"),
            "url": it.get("URL"),
            "pdf_url": pdf_url,
            "abstract": it.get("abstract"),  # often None; Crossref sometimes has JATS XML
            "is_open_access": bool(pdf_url),
        })

    return {
        "total": total,
        "count": len(results),
        "offset": offset,
        "results": results,
    }

async def fetch_arxiv(query: str, rows: int, offset: int) -> Dict[str, Any]:
    """
    arXiv: fast preprint search with abstracts.
    API returns Atom; we parse with feedparser.
    Docs: https://info.arxiv.org/help/api/user-manual.html
    """
    # arXiv uses 'start' + 'max_results'
    base = "https://export.arxiv.org/api/query"
    q = f"all:{query}"
    params = {
        "search_query": q,
        "start": max(offset, 0),
        "max_results": min(rows, 100),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    # Build URL manually so users can replicate easily
    url = base + "?" + urllib.parse.urlencode(params)

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "copaco-research-fn/1.0"})
        r.raise_for_status()
        feed_text = r.text

    feed = feedparser.parse(feed_text)
    entries = feed.get("entries", []) or []

    results: List[Dict[str, Any]] = []
    for e in entries:
        authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
        pdf_url = None
        html_url = e.get("link")
        for l in e.get("links", []):
            if l.get("type") == "application/pdf":
                pdf_url = l.get("href")
                break
        # arXiv IDs look like 2101.00001
        arxiv_id = (e.get("id") or "").split("/")[-1]

        # Year from published date (e.g., 2024-05-02T…)
        year = None
        if e.get("published"):
            try:
                year = int(e["published"][0:4])
            except Exception:
                pass

        results.append({
            "source": "arxiv",
            "id": arxiv_id,
            "title": e.get("title", "").strip(),
            "authors": authors,
            "venue": "arXiv",
            "categories": e.get("tags", []),
            "year": year,
            "doi": None,  # sometimes present in 'arxiv_doi' extension, but not in all feeds
            "url": html_url,
            "pdf_url": pdf_url,
            "abstract": (e.get("summary") or "").strip(),
            "is_open_access": True,
        })

    # arXiv doesn't return a hard total in Atom; approximate pagination
    return {
        "total": None,
        "count": len(results),
        "offset": offset,
        "results": results,
    }

# -------- Azure Function (HTTP Trigger)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.function_name(name="papers")
@app.route(route="papers", methods=["GET", "OPTIONS"])
async def papers(req: func.HttpRequest) -> func.HttpResponse:
    # Handle CORS preflight quickly
    if req.method == "OPTIONS":
        return _ok({"ok": True})

    query = (req.params.get("q") or req.params.get("query") or "").strip()
    if not query:
        return _bad_request("Please provide a 'q' query parameter, e.g. ?q=graph%20neural%20networks")

    # Optional: choose data source
    source = (req.params.get("source") or "crossref").lower()
    rows = max(1, min(int(req.params.get("limit", "20")), 100))
    offset = max(0, int(req.params.get("offset", "0")))

    try:
        if source == "arxiv":
            data = await fetch_arxiv(query, rows, offset)
        elif source == "crossref":
            data = await fetch_crossref(query, rows, offset)
        else:
            # Support a simple "both" mode that merges and trims
            if source in ("both", "all"):
                a, c = await fetch_arxiv(query, rows, offset), await fetch_crossref(query, rows, offset)
                merged = (a["results"] or []) + (c["results"] or [])
                # Simple dedupe by (title,url)
                seen = set()
                unique: List[Dict[str, Any]] = []
                for item in merged:
                    key = (item.get("title", "").lower().strip(), item.get("url"))
                    if key not in seen:
                        seen.add(key)
                        unique.append(item)
                data = {
                    "total": None,
                    "count": len(unique[:rows]),
                    "offset": offset,
                    "results": unique[:rows],
                }
            else:
                return _bad_request("Unknown 'source'. Use 'crossref', 'arxiv', or 'both'.")

        # Attach echo metadata
        data["query"] = query
        data["source"] = source
        return _ok(data)

    except httpx.HTTPStatusError as e:
        return _bad_request(f"Upstream error from {source}: {e.response.status_code} {e.response.text[:200]}")
    except Exception as e:
        return _bad_request(f"Unexpected error: {type(e).__name__}: {e}")
