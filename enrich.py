"""Enrich Aadam Jacobs collection bands and venues with Wikipedia/Wikidata data.

Reads distinct creators (bands) and venues from shows.db, looks each up on
Wikipedia, validates that the match is plausible, and caches the result in the
band_info / venue_info tables. Re-runs skip names already cached (unless they
are listed in overrides.json, which always forces a re-fetch).

Usage:
    uv run enrich.py                 # enrich all bands and venues
    uv run enrich.py --bands         # bands only
    uv run enrich.py --venues        # venues only
    uv run enrich.py --limit 20      # only the top-N (by show count), for testing
    uv run enrich.py --refresh       # ignore cache, re-fetch everything
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

DB_PATH = "shows.db"
OVERRIDES_PATH = "overrides.json"

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Follows the Wikimedia User-Agent policy format:
#   "Client name/version (contact) library/version"
# Contact is a URL (an email may be added); see foundation.wikimedia.org User-Agent policy.
USER_AGENT = (
    "archive-search/0.1 "
    "(https://github.com/bwbensonjr/archive-search) "
    f"python-requests/{requests.__version__}"
)
REQUEST_DELAY = 0.4  # base seconds between HTTP calls, to be polite
MAX_RETRIES = 5      # attempts before giving up on a 429/5xx/maxlag
MAX_BACKOFF = 60     # cap for exponential backoff, seconds
MAXLAG = 5           # action-API maxlag: defer our reads when servers are lagged

# Words in the Wikipedia "description"/extract that signal a plausible match.
BAND_TERMS = [
    "band", "musician", "singer", "songwriter", "rapper", "duo", "group",
    "trio", "quartet", "ensemble", "orchestra", "dj", "guitarist", "drummer",
    "bassist", "vocalist", "music", "discography", "album", "record producer",
    "composer", "rock", "pop", "hip hop", "jazz", "punk", "metal", "folk",
]
VENUE_TERMS = [
    "venue", "club", "nightclub", "theatre", "theater", "arena", "hall",
    "festival", "auditorium", "stadium", "amphitheatre", "amphitheater",
    "ballroom", "bar", "pub", "concert", "music venue", "building", "park",
]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def is_maxlag(resp):
    """True if an action-API response is a maxlag (server lagged) error.

    MediaWiki signals server lag with an error code of "maxlag" (and a
    Retry-After header) rather than a distinct HTTP status, so we inspect
    the body. Non-JSON responses (e.g. the REST summary endpoint) are not
    action-API calls and never match.
    """
    try:
        return resp.json().get("error", {}).get("code") == "maxlag"
    except ValueError:
        return False


def http_get(url, params=None, action_api=False):
    """GET with a polite delay and exponential backoff on 429/5xx/maxlag.

    Honors a numeric Retry-After header when present. Returns the final
    response; the caller is responsible for status handling (e.g. 404).
    """
    for attempt in range(MAX_RETRIES):
        resp = session.get(url, params=params, timeout=20)
        time.sleep(REQUEST_DELAY)
        throttled = (
            resp.status_code == 429
            or resp.status_code >= 500
            or (action_api and is_maxlag(resp))
        )
        if not throttled:
            return resp
        if attempt == MAX_RETRIES - 1:
            return resp  # give up; hand back the last (error) response
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.isdigit():
            wait = int(retry_after)
        else:
            wait = min(2 ** attempt, MAX_BACKOFF)
        print(f"    {resp.status_code} (attempt {attempt + 1}/{MAX_RETRIES}); "
              f"backing off {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"unreachable: retry loop exited for {url}")


def load_overrides():
    """Load manual title corrections. Maps name -> wikipedia title.

    An empty-string value forces "no match" (suppress a bad auto-match).
    """
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        return json.load(f)


def create_tables(conn):
    """Create the enrichment tables if they do not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS band_info (
            name TEXT PRIMARY KEY,
            wikipedia_title TEXT,
            wikipedia_url TEXT,
            extract TEXT,
            thumbnail_url TEXT,
            description TEXT,
            wikidata_id TEXT,
            genres TEXT,
            origin TEXT,
            formed_year TEXT,
            matched INTEGER,
            fetched_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS venue_info (
            name TEXT PRIMARY KEY,
            wikipedia_title TEXT,
            wikipedia_url TEXT,
            extract TEXT,
            thumbnail_url TEXT,
            description TEXT,
            wikidata_id TEXT,
            location TEXT,
            matched INTEGER,
            fetched_at TEXT
        )
    """)
    conn.commit()


def distinct_names(conn, column, limit=None):
    """Return distinct non-empty band/venue names ordered by show count desc."""
    sql = (
        f"SELECT {column}, COUNT(*) AS n FROM show "
        f"WHERE {column} IS NOT NULL AND {column} != '' "
        f"GROUP BY {column} ORDER BY n DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [row[0] for row in conn.execute(sql).fetchall()]


def already_cached(conn, table, name):
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def wiki_search_title(query):
    """Return the best-guess Wikipedia article title for a query, or None."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 1,
        "format": "json",
        "maxlag": MAXLAG,
    }
    resp = http_get(WIKI_API, params=params, action_api=True)
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def wiki_summary(title):
    """Return the REST summary dict for a title, or None if missing."""
    url = WIKI_SUMMARY.format(quote(title.replace(" ", "_"), safe=""))
    resp = http_get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    # Disambiguation / list pages are not useful matches.
    if data.get("type") == "disambiguation":
        return None
    return data


def is_relevant(summary, terms):
    """Heuristic: does the summary look like the right kind of subject?"""
    haystack = " ".join([
        summary.get("description", "") or "",
        summary.get("extract", "") or "",
    ]).lower()
    return any(term in haystack for term in terms)


def wikidata_details(qid):
    """Return (genres, origin, formed_year) best-effort from a Wikidata entity."""
    if not qid:
        return ("", "", "")
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims",
        "format": "json",
        "maxlag": MAXLAG,
    }
    resp = http_get(WIKIDATA_API, params=params, action_api=True)
    resp.raise_for_status()
    entity = resp.json().get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})

    def claim_qids(prop):
        out = []
        for c in claims.get(prop, []):
            try:
                out.append(c["mainsnak"]["datavalue"]["value"]["id"])
            except (KeyError, TypeError):
                continue
        return out

    def claim_year(prop):
        for c in claims.get(prop, []):
            try:
                t = c["mainsnak"]["datavalue"]["value"]["time"]
                m = re.search(r"(\d{4})", t)
                if m:
                    return m.group(1)
            except (KeyError, TypeError):
                continue
        return ""

    genre_qids = claim_qids("P136")  # genre
    # country of origin (bands), location of formation, located in admin entity
    origin_qids = (
        claim_qids("P495") or claim_qids("P740") or claim_qids("P131")
    )
    formed_year = claim_year("P571")  # inception

    labels = resolve_labels(genre_qids + origin_qids[:1])
    genres = ", ".join(labels.get(q, "") for q in genre_qids if labels.get(q))
    origin = labels.get(origin_qids[0], "") if origin_qids else ""
    return (genres, origin, formed_year)


def resolve_labels(qids):
    """Batch-resolve Wikidata QIDs to English labels."""
    qids = [q for q in qids if q]
    if not qids:
        return {}
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids[:50]),
        "props": "labels",
        "languages": "en",
        "format": "json",
        "maxlag": MAXLAG,
    }
    resp = http_get(WIKIDATA_API, params=params, action_api=True)
    resp.raise_for_status()
    entities = resp.json().get("entities", {})
    labels = {}
    for qid, ent in entities.items():
        label = ent.get("labels", {}).get("en", {}).get("value")
        if label:
            labels[qid] = label
    return labels


def lookup(name, kind, overrides):
    """Look up one name. Returns a dict of fields for the *_info table."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    terms = BAND_TERMS if kind == "band" else VENUE_TERMS
    blank = {
        "name": name, "wikipedia_title": "", "wikipedia_url": "", "extract": "",
        "thumbnail_url": "", "description": "", "wikidata_id": "",
        "matched": 0, "fetched_at": now,
    }
    if kind == "band":
        blank.update({"genres": "", "origin": "", "formed_year": ""})
    else:
        blank.update({"location": ""})

    # Manual override: explicit title, or "" to force no-match.
    override = overrides.get(name)
    if override is not None:
        if override == "":
            return blank
        title = override
        forced = True
    else:
        suffix = " band" if kind == "band" else " music venue"
        title = wiki_search_title(name + suffix) or wiki_search_title(name)
        forced = False

    if not title:
        return blank

    summary = wiki_summary(title)
    if not summary:
        return blank
    if not forced and not is_relevant(summary, terms):
        # Auto-match did not look like the right kind of thing.
        return blank

    result = dict(blank)
    result.update({
        "wikipedia_title": summary.get("title", title),
        "wikipedia_url": summary.get("content_urls", {})
            .get("desktop", {}).get("page", ""),
        "extract": summary.get("extract", ""),
        "thumbnail_url": summary.get("thumbnail", {}).get("source", ""),
        "description": summary.get("description", ""),
        "wikidata_id": summary.get("wikibase_item", ""),
        "matched": 1,
    })
    genres, origin, formed_year = wikidata_details(result["wikidata_id"])
    if kind == "band":
        result.update({
            "genres": genres, "origin": origin, "formed_year": formed_year,
        })
    else:
        result.update({"location": origin})
    return result


def upsert(conn, table, row):
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in cols],
    )
    conn.commit()


def enrich(conn, kind, names, overrides, refresh):
    table = "band_info" if kind == "band" else "venue_info"
    total = len(names)
    matched = 0
    for i, name in enumerate(names, 1):
        if not refresh and name not in overrides and already_cached(conn, table, name):
            continue
        try:
            row = lookup(name, kind, overrides)
        except requests.RequestException as exc:
            print(f"  [{i}/{total}] {name!r}: request failed ({exc})", file=sys.stderr)
            continue
        upsert(conn, table, row)
        matched += row["matched"]
        status = "ok" if row["matched"] else "no match"
        print(f"  [{i}/{total}] {name!r} -> {row['wikipedia_title'] or status}")
    print(f"{kind}s: cached {total} names this run ({matched} newly matched)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bands", action="store_true", help="enrich bands only")
    parser.add_argument("--venues", action="store_true", help="enrich venues only")
    parser.add_argument("--limit", type=int, help="top-N by show count (testing)")
    parser.add_argument("--refresh", action="store_true", help="ignore cache")
    args = parser.parse_args()

    do_bands = args.bands or not args.venues
    do_venues = args.venues or not args.bands

    overrides = load_overrides()
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)

    if do_bands:
        print("Enriching bands...")
        bands = distinct_names(conn, "creator", args.limit)
        enrich(conn, "band", bands, overrides, args.refresh)
    if do_venues:
        print("Enriching venues...")
        venues = distinct_names(conn, "venue", args.limit)
        enrich(conn, "venue", venues, overrides, args.refresh)

    conn.close()


if __name__ == "__main__":
    main()
