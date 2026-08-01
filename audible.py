"""
audible.py – Search the public Audible catalog API for audiobook metadata.

No API key required. Unlike Open Library, results include narrator and
proper audiobook series/cover data.
"""

import re
from typing import Optional

import requests

SEARCH_URL = "https://api.audible.com/1.0/catalog/products"
TIMEOUT    = 12


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text or '').strip()


def search_books(query: str, limit: int = 25) -> list:
    params = {
        'keywords':         query,
        'num_results':      limit,
        'products_sort_by': 'Relevance',
        'response_groups':  ('contributors,media,product_attrs,'
                             'product_desc,product_extended_attrs,series'),
        'image_sizes':      '500',
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        products = resp.json().get('products', [])
    except Exception as exc:
        raise RuntimeError(f"Audible search failed: {exc}") from exc

    results = []
    for p in products:
        series_name, series_num = '', ''
        series = p.get('series') or []
        if series:
            series_name = series[0].get('title', '') or ''
            series_num  = str(series[0].get('sequence', '') or '')
        release = p.get('release_date', '') or ''
        images  = p.get('product_images') or {}
        cover_url = images.get('500') or next(iter(images.values()), '')

        results.append({
            'title':       p.get('title', ''),
            'author':      ', '.join(a.get('name', '') for a in (p.get('authors') or [])),
            'narrator':    ', '.join(n.get('name', '') for n in (p.get('narrators') or [])),
            'series':      series_name,
            'series_num':  series_num,
            'year':        release[:4],
            'publisher':   p.get('publisher_name', '') or '',
            'description': _strip_html(p.get('merchandising_summary', '')),
            'cover_url':   cover_url,
            'asin':        p.get('asin', ''),
        })
    return results


def fetch_cover(url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code == 200 and len(resp.content) > 500:
            return resp.content
    except Exception as exc:
        print(f"[audible] cover fetch error: {exc}")
    return None
