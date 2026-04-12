"""Fetch Aadam Jacobs collection shows from Internet Archive into SQLite."""

import json
import re
import sqlite3
import subprocess
DB_PATH = "shows.db"
SEARCH_QUERY = "collection:aadamjacobs"
FIELDS = ["identifier", "date", "title", "venue", "creator"]

VENUE_RE = re.compile(r"Live at (.+?) \d{4}-\d{2}-\d{2}")


def fetch_shows():
    """Run ia search and return parsed JSON lines."""
    cmd = ["ia", "search", SEARCH_QUERY]
    for field in FIELDS:
        cmd.extend(["-f", field])

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    shows = []
    for line in result.stdout.strip().splitlines():
        item = json.loads(line)
        shows.append(item)
    return shows


def extract_venue_from_title(title):
    """Parse venue from title like 'Band Live at Venue 2013-09-22'."""
    match = VENUE_RE.search(title or "")
    return match.group(1).strip() if match else ""


def clean_date(raw_date):
    """Extract YYYY-MM-DD from ISO date string."""
    if not raw_date:
        return ""
    return raw_date[:10]


def create_db(shows):
    """Create SQLite database and insert shows."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS show")
    cur.execute("""
        CREATE TABLE show (
            identifier TEXT PRIMARY KEY,
            title TEXT,
            creator TEXT,
            date TEXT,
            venue TEXT,
            url TEXT
        )
    """)

    for item in shows:
        identifier = item.get("identifier", "")
        title = item.get("title", "")
        creator = item.get("creator", "")
        date = clean_date(item.get("date", ""))
        venue = item.get("venue", "") or ""
        if not venue:
            venue = extract_venue_from_title(title)
        url = f"https://archive.org/details/{identifier}"

        cur.execute(
            "INSERT OR REPLACE INTO show (identifier, title, creator, date, venue, url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identifier, title, creator, date, venue, url),
        )

    conn.commit()
    conn.close()
    return len(shows)


def main():
    print("Fetching shows from Internet Archive...")
    shows = fetch_shows()
    print(f"Fetched {len(shows)} items")

    count = create_db(shows)
    print(f"Inserted {count} rows into {DB_PATH}")


if __name__ == "__main__":
    main()
