"""Export shows.db (+ Wikipedia enrichment) to static JSON for the docs/ site.

Joins the show table with band_info / venue_info and writes the three JSON
files the client-side app consumes. Run after fetch_shows.py and enrich.py.

Usage:
    uv run build_site.py
"""

import json
import os
import re
import sqlite3
from collections import Counter

DB_PATH = "shows.db"
OUT_DIR = os.path.join("docs", "data")


def slugify(text):
    """Turn a name into a URL-safe hash-route slug."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def fetch_info(conn, table):
    """Return {name: dict} for a *_info table, or {} if the table is absent."""
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return {}
    cols = [c[0] for c in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    return {row[cols.index("name")]: dict(zip(cols, row)) for row in rows}


def wiki_block(info):
    """Shared Wikipedia sub-object for a band/venue, or None if unmatched."""
    if not info or not info.get("matched"):
        return None
    return {
        "title": info.get("wikipedia_title", ""),
        "url": info.get("wikipedia_url", ""),
        "extract": info.get("extract", ""),
        "thumbnail": info.get("thumbnail_url", ""),
        "description": info.get("description", ""),
    }


def build():
    conn = sqlite3.connect(DB_PATH)
    band_info = fetch_info(conn, "band_info")
    venue_info = fetch_info(conn, "venue_info")

    rows = conn.execute(
        "SELECT identifier, title, creator, date, venue, url FROM show"
    ).fetchall()
    conn.close()

    # Ensure slug uniqueness across distinct names that collapse to one slug.
    band_slugs, venue_slugs = {}, {}

    def unique_slug(name, registry):
        if name in registry:
            return registry[name]
        base = slugify(name)
        slug, n = base, 2
        used = set(registry.values())
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        registry[name] = slug
        return slug

    shows = []
    band_counts = Counter()
    venue_counts = Counter()
    for identifier, title, creator, date, venue, url in rows:
        creator = creator or "Unknown"
        venue = venue or ""
        band_slug = unique_slug(creator, band_slugs)
        venue_slug = unique_slug(venue, venue_slugs) if venue else ""
        year = date[:4] if date else ""
        shows.append({
            "identifier": identifier,
            "title": title or "",
            "band": creator,
            "band_slug": band_slug,
            "venue": venue,
            "venue_slug": venue_slug,
            "date": date or "",
            "year": year,
            "url": url or f"https://archive.org/details/{identifier}",
        })
        band_counts[creator] += 1
        if venue:
            venue_counts[venue] += 1

    bands = []
    for name, slug in band_slugs.items():
        info = band_info.get(name, {})
        bands.append({
            "name": name,
            "slug": slug,
            "count": band_counts[name],
            "wikipedia": wiki_block(info),
            "genres": info.get("genres", ""),
            "origin": info.get("origin", ""),
            "formed_year": info.get("formed_year", ""),
        })
    bands.sort(key=lambda b: (-b["count"], b["name"].lower()))

    venues = []
    for name, slug in venue_slugs.items():
        info = venue_info.get(name, {})
        venues.append({
            "name": name,
            "slug": slug,
            "count": venue_counts[name],
            "wikipedia": wiki_block(info),
            "location": info.get("location", ""),
        })
    venues.sort(key=lambda v: (-v["count"], v["name"].lower()))

    os.makedirs(OUT_DIR, exist_ok=True)
    write_json("shows.json", shows)
    write_json("bands.json", bands)
    write_json("venues.json", venues)

    matched_bands = sum(1 for b in bands if b["wikipedia"])
    matched_venues = sum(1 for v in venues if v["wikipedia"])
    print(f"Wrote {len(shows)} shows, {len(bands)} bands "
          f"({matched_bands} with Wikipedia), "
          f"{len(venues)} venues ({matched_venues} with Wikipedia) to {OUT_DIR}/")


def write_json(filename, data):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    build()
