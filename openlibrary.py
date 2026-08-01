"""
openlibrary.py – Fetch metadata and cover art from the Open Library API.

No API key required.
"""

import re
from typing import Optional

import requests

SEARCH_URL = "https://openlibrary.org/search.json"
WORKS_URL  = "https://openlibrary.org{key}.json"
COVER_URL  = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
TIMEOUT    = 12

# Patterns like "Mistborn, #1"  /  "Mistborn #1"  /  "Mistborn, Book 1"
_SER_PAT = re.compile(
    r'^(.+?)\s*(?:,\s*)?(?:#|[Bb]ook\s+)(\d+\.?\d*)$')


def _split_series(raw: str) -> tuple:
    """Return (series_name, series_num_str) from a raw OL series string."""
    if not raw:
        return '', ''
    m = _SER_PAT.match(raw.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw.strip(), ''


def search_books(query: str, limit: int = 25) -> list:
    params = {
        'q':      query,
        'fields': ('key,title,author_name,first_publish_year,'
                   'publisher,subject,series,cover_i,isbn'),
        'limit':  limit,
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        docs = resp.json().get('docs', [])
    except Exception as exc:
        raise RuntimeError(f"Open Library search failed: {exc}") from exc

    results = []
    for doc in docs:
        publishers = doc.get('publisher') or []
        subjects   = doc.get('subject')   or []
        series_lst = doc.get('series')    or []
        isbn_lst   = doc.get('isbn')      or []

        raw_series = series_lst[0] if series_lst else ''
        series_name, series_num = _split_series(raw_series)

        # If series_num still empty, try fetching work details for position
        work_key = doc.get('key', '')
        if series_name and not series_num and work_key:
            series_num = _fetch_series_position(work_key, series_name)

        results.append({
            'key':        work_key,
            'title':      doc.get('title', ''),
            'author':     ', '.join(doc.get('author_name') or []),
            'year':       str(doc.get('first_publish_year', '')),
            'publisher':  publishers[0] if publishers else '',
            'genre':      ', '.join(subjects[:3]),
            'series':     series_name,
            'series_num': series_num,
            'cover_id':   doc.get('cover_i'),
            'isbn':       isbn_lst[0] if isbn_lst else '',
        })

    return results


def _fetch_series_position(work_key: str, series_name: str) -> str:
    """
    Try to get the series position from the work's detail page.
    Returns '' on failure.
    """
    try:
        url = WORKS_URL.format(key=work_key)
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return ''
        data = resp.json()
        # OL work JSON sometimes has {"series": ["Mistborn, #1"]} or
        # {"series": [{"name": "Mistborn", "position": "1"}]}
        for entry in data.get('series') or []:
            if isinstance(entry, str):
                name, num = _split_series(entry)
                if num:
                    return num
            elif isinstance(entry, dict):
                pos = str(entry.get('position') or '')
                if pos:
                    return pos
    except Exception:
        pass
    return ''


def fetch_cover(cover_id: int) -> Optional[bytes]:
    url = COVER_URL.format(cover_id=cover_id)
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code == 200 and len(resp.content) > 500:
            return resp.content
    except Exception as exc:
        print(f"[openlibrary] cover fetch error: {exc}")
    return None
