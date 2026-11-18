"""
Offline Wikipedia Reader & MCP Service.

Provides:
1. Direct Python API for searching and summarizing articles from the offline ZIM archive.
2. FastMCP server exposing tools for Claude / Antigravity / Agent RAG workflows.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from settings_manager import load_settings, resolve_path

# Resolve default ZIM archive path safely across platforms
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ZIM_PATH = os.path.join(BASE_DIR, "data", "zim", "wikipedia_en_all_nopic_2026-06.zim")
ZIM_PATH = os.getenv("ZIM_PATH", DEFAULT_ZIM_PATH)

_ARCHIVE_CACHE: Dict[str, tuple[int, Archive]] = {}


def configured_zim_path(settings: Optional[Dict[str, Any]] = None) -> str:
    settings = load_settings() if settings is None else settings
    return resolve_path(settings.get("zim_path") or os.getenv("ZIM_PATH") or DEFAULT_ZIM_PATH)


def wikipedia_available(settings: Optional[Dict[str, Any]] = None) -> bool:
    settings = load_settings() if settings is None else settings
    if not settings.get("wikipedia_enabled") or not os.path.isfile(configured_zim_path(settings)):
        return False
    try:
        get_archive(configured_zim_path(settings))
        return True
    except Exception:
        return False


def get_archive(path: Optional[str] = None) -> Archive:
    """
    Returns an Archive handle for the given ZIM path, caching open handles.
    """
    target_path = resolve_path(path or configured_zim_path())
    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Wikipedia ZIM archive not found at: {target_path}")

    modified_at = os.stat(target_path).st_mtime_ns
    cached = _ARCHIVE_CACHE.get(target_path)
    if cached is None or cached[0] != modified_at:
        from libzim.reader import Archive

        cached = (modified_at, Archive(target_path))
        _ARCHIVE_CACHE[target_path] = cached
    return cached[1]


def clean_html_to_paragraphs(raw_html: str, min_length: int = 40) -> List[str]:
    """
    Strips citations, scripts, styles, and extracts readable text paragraphs.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    # Decompose non-content and citation tags
    for tag in soup(["script", "style", "sup", "noscript", "table"]):
        tag.decompose()

    paragraphs: List[str] = []
    for p in soup.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        # Clean extra whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Filter out stubby link rows or trivial snippets
        if len(text) >= min_length:
            paragraphs.append(text)
    return paragraphs


def get_wikipedia_summary(
    query: str,
    max_paragraphs: int = 1,
    max_chars: int = 1000,
    zim_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Searches the offline Wikipedia archive for a topic and extracts a clean,
    concise reference summary.

    Resolution Strategy:
    1. Direct title match or title redirect (e.g. 'Machine Learning' -> 'Machine learning').
    2. Variations (title case, lowercase, capitalize).
    3. Full-text search fallback using libzim Searcher.
    """
    clean_query = query.strip()
    if not clean_query:
        return {
            "query": query,
            "title": "",
            "summary": "No query provided.",
            "paragraphs": [],
            "found": False,
            "source": "Offline Wikipedia",
        }

    try:
        zim = get_archive(zim_path)
    except Exception as e:
        return {
            "query": query,
            "title": "",
            "summary": f"Could not access Wikipedia ZIM archive: {str(e)}",
            "paragraphs": [],
            "found": False,
            "source": "Offline Wikipedia",
        }

    entry = None

    # Step 1: Check title matches (exact and common case variations)
    candidate_titles = [
        clean_query,
        clean_query.capitalize(),
        clean_query.title(),
        clean_query.lower(),
        clean_query.replace(" ", "_"),
    ]

    for cand in candidate_titles:
        if zim.has_entry_by_title(cand):
            entry = zim.get_entry_by_title(cand)
            break

    # Resolve redirects if found
    if entry and entry.is_redirect:
        try:
            entry = entry.get_redirect_entry()
        except Exception:
            pass

    # Step 2: Fallback to full-text search if no exact title matched
    if not entry:
        try:
            from libzim.search import Query, Searcher

            q = Query().set_query(clean_query)
            searcher = Searcher(zim)
            search = searcher.search(q)
            results = list(search.getResults(0, 5))
            if results:
                # In libzim 3.x getResults yields path strings (or objects with .path)
                for res in results:
                    path = getattr(res, "path", res)
                    try:
                        cand_entry = zim.get_entry_by_path(path)
                        if cand_entry.is_redirect:
                            cand_entry = cand_entry.get_redirect_entry()
                        # Prefer non-outline / main articles
                        entry = cand_entry
                        if not entry.title.lower().startswith("outline of"):
                            break
                    except Exception:
                        continue
        except Exception as e:
            return {
                "query": query,
                "title": "",
                "summary": f"Search error during query '{clean_query}': {str(e)}",
                "paragraphs": [],
                "found": False,
                "source": "Offline Wikipedia",
            }

    if not entry:
        return {
            "query": query,
            "title": "",
            "summary": f"No article found for '{clean_query}' in the offline Wikipedia archive.",
            "paragraphs": [],
            "found": False,
            "source": "Offline Wikipedia",
        }

    # Step 3: Extract and clean HTML content
    try:
        item = entry.get_item()
        raw_html = bytes(item.content).decode("UTF-8", errors="replace")
        paragraphs = clean_html_to_paragraphs(raw_html)

        if not paragraphs:
            summary = "Article found, but no text paragraphs could be extracted."
        else:
            selected_paras = paragraphs[:max_paragraphs]
            summary = "\n\n".join(selected_paras)
            if len(summary) > max_chars:
                summary = summary[:max_chars].rstrip() + "..."

        return {
            "query": query,
            "title": entry.title,
            "path": getattr(entry, "path", ""),
            "summary": summary,
            "paragraphs": paragraphs[:max_paragraphs],
            "found": True,
            "source": "Offline Wikipedia (wikipedia_en_all_nopic)",
        }
    except Exception as e:
        return {
            "query": query,
            "title": getattr(entry, "title", clean_query),
            "summary": f"Error extracting content for '{clean_query}': {str(e)}",
            "paragraphs": [],
            "found": False,
            "source": "Offline Wikipedia",
        }


# ==============================================================================
# FASTMCP TOOLS (Exposed to Claude / Antigravity Agents)
# ==============================================================================

def search_wiki(query: str, limit: int = 3) -> str:
    """
    Search the offline Wikipedia ZIM archive for study topics.
    Returns the top matching article texts.
    """
    try:
        zim = get_archive()
        from libzim.search import Query, Searcher

        q = Query().set_query(query)
        searcher = Searcher(zim)
        search = searcher.search(q)
        results = list(search.getResults(0, limit))

        if not results:
            return f"No results found for '{query}'."

        formatted_results = []
        for res in results:
            path = getattr(res, "path", res)
            try:
                entry = zim.get_entry_by_path(path)
                if entry.is_redirect:
                    entry = entry.get_redirect_entry()
                item = entry.get_item()
                raw_html = bytes(item.content).decode("UTF-8", errors="replace")
                paras = clean_html_to_paragraphs(raw_html)
                summary = "\n\n".join(paras[:2])[:1200]
                if not summary:
                    summary = "(Content available in archive)"
                formatted_results.append(f"Title: {entry.title}\nContent:\n{summary}")
            except Exception as item_err:
                formatted_results.append(f"Path: {path}\nError reading item: {item_err}")

        return "\n\n---\n\n".join(formatted_results)
    except Exception as e:
        return f"ZIM Database error occurred: {str(e)}"


def get_article_summary_mcp(query: str) -> str:
    """
    Retrieves a short, clean reference summary for a study topic from the offline Wikipedia archive.
    """
    res = get_wikipedia_summary(query, max_paragraphs=3)
    if not res.get("found"):
        return f"Could not find summary for '{query}': {res.get('summary')}"
    return f"=== Wikipedia Summary: {res['title']} ===\n\n{res['summary']}"


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if "--mcp" in sys.argv:
        from fastmcp import FastMCP

        mcp = FastMCP("Offline Wikipedia")
        mcp.tool(search_wiki)
        mcp.tool(get_article_summary_mcp)
        mcp.run()
        sys.exit()

    print(f"[i] Loading archive from: {configured_zim_path()}")
    test_result = get_wikipedia_summary("Machine Learning")
    print(f"\n[✓] Found: {test_result['found']} | Title: {test_result['title']}")
    print("\n--- Summary Excerpt ---")
    print(test_result["summary"])
    print("-----------------------")