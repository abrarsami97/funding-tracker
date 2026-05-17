"""
Funding news pipeline — main entry point.

Usage:
    python main.py                       # last 7 days, writes to SQLite
    python main.py --days 14             # last 14 days
    python main.py --enrich              # also fetch company websites (slower)
    python main.py --enrich --workers 8  # tune parallelism
    python main.py --csv funding.csv     # also export a CSV alongside SQLite

Cron (every 12 hours):
    0 */12 * * * cd /path/to/funding-tracker && python3 main.py --enrich >> logs/pipeline.log 2>&1
"""
import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fetchers.techcrunch import TechCrunchFundingFetcher
from fetchers.vcnewsdaily import VCNewsDailyFetcher
from fetchers.techfundingnews import TechFundingNewsFetcher
from fetchers.techstartups import TechStartupsFetcher
from fetchers.finsmes import FinSMEsFetcher
from fetchers.sifted import SiftedFetcher
from fetchers.inc42 import Inc42Fetcher
from fetchers.eustartups import EUStartupsFetcher

from extractor import extract
from enricher import enrich_entries


FETCHERS = [
    TechCrunchFundingFetcher(),
    VCNewsDailyFetcher(),
    TechFundingNewsFetcher(),
    TechStartupsFetcher(),
    FinSMEsFetcher(),
    SiftedFetcher(),
    Inc42Fetcher(),
    EUStartupsFetcher(),
]

DB_PATH = Path('funding_tracker.db')

DB_COLUMNS = [
    'published_date',
    'source',
    'company_name',
    'funding_amount',
    'currency',
    'round_stage',
    'industry',
    'country',
    'investors',
    'company_website',
    'title',
    'url',
    'summary',
    'last_updated',
]

CSV_COLUMNS = DB_COLUMNS[:-1]  # exclude last_updated from CSV


# ─────────────────────────────────────────────────────────────────────
# SQLite helpers
# ─────────────────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> sqlite3.Connection:
    """Create the deals table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            published_date TEXT,
            source         TEXT,
            company_name   TEXT,
            funding_amount REAL,
            currency       TEXT,
            round_stage    TEXT,
            industry       TEXT,
            country        TEXT,
            investors      TEXT,
            company_website TEXT,
            title          TEXT,
            url            TEXT UNIQUE,   -- deduplicate by URL
            summary        TEXT,
            last_updated   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_published ON deals(published_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source    ON deals(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_url       ON deals(url)")
    conn.commit()
    return conn


def _upsert_deals(conn: sqlite3.Connection, deals: list, last_updated: str):
    """Insert new deals, update existing ones (matched by URL)."""
    inserted = updated = 0
    for deal in deals:
        row = {col: deal.get(col) for col in DB_COLUMNS}
        row['last_updated'] = last_updated

        # Convert None → empty string for text fields so SQLite stores cleanly
        for col in ['company_name', 'currency', 'round_stage', 'industry',
                    'country', 'investors', 'company_website', 'summary']:
            if row.get(col) is None:
                row[col] = ''

        existing = conn.execute(
            "SELECT id FROM deals WHERE url = ?", (row['url'],)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE deals SET
                    published_date=:published_date, source=:source,
                    company_name=:company_name, funding_amount=:funding_amount,
                    currency=:currency, round_stage=:round_stage,
                    industry=:industry, country=:country,
                    investors=:investors, company_website=:company_website,
                    title=:title, summary=:summary, last_updated=:last_updated
                WHERE url=:url
            """, row)
            updated += 1
        else:
            conn.execute("""
                INSERT INTO deals
                    (published_date, source, company_name, funding_amount,
                     currency, round_stage, industry, country,
                     investors, company_website, title, url,
                     summary, last_updated)
                VALUES
                    (:published_date, :source, :company_name, :funding_amount,
                     :currency, :round_stage, :industry, :country,
                     :investors, :company_website, :title, :url,
                     :summary, :last_updated)
            """, row)
            inserted += 1

    conn.commit()
    return inserted, updated


# ─────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────

def run(days_back=7, csv_path=None, enrich=False, workers=8):
    since_date = datetime.now(timezone.utc) - timedelta(days=days_back)
    last_updated = datetime.now(timezone.utc).isoformat()

    print(f"Funding tracker — pulling articles since {since_date.date()}")
    print(f"({days_back} days back)\n")

    # ── Fetch ──────────────────────────────────────────────────────
    all_entries = []
    for fetcher in FETCHERS:
        print(f"  → {fetcher.name:<22}", end=' ', flush=True)
        try:
            entries = fetcher.fetch(since_date)
            print(f"{len(entries):>3} entries")
            all_entries.extend(entries)
        except Exception as e:
            print(f"FAILED  ({type(e).__name__}: {e})")

    print(f"\nRaw entries collected: {len(all_entries)}")

    # ── Extract + filter ───────────────────────────────────────────
    extracted = []
    for entry in all_entries:
        result = extract(entry)
        if result:
            extracted.append(result)
    print(f"After funding-article filter: {len(extracted)}")

    # ── Deduplicate by URL ─────────────────────────────────────────
    seen = set()
    deduped = []
    for e in extracted:
        url = e.get('url', '')
        if url and url not in seen:
            seen.add(url)
            deduped.append(e)
    print(f"After URL dedup: {len(deduped)}")

    # ── Enrich (company websites via LLM) ──────────────────────────
    if enrich:
        print(f"\nEnriching with company websites ({workers} workers)...")
        enrich_entries(deduped, max_workers=workers)

    # ── Sort newest first ──────────────────────────────────────────
    deduped.sort(
        key=lambda x: x.get('published_date') or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # ── Stringify datetime ─────────────────────────────────────────
    for e in deduped:
        pd = e.get('published_date')
        e['published_date'] = pd.isoformat() if hasattr(pd, 'isoformat') else (pd or '')

    # ── Write to SQLite (primary output) ──────────────────────────
    conn = _init_db(DB_PATH)
    inserted, updated = _upsert_deals(conn, deduped, last_updated)
    conn.close()
    print(f"\n✓ SQLite: {inserted} new rows inserted, {updated} updated → {DB_PATH.resolve()}")

    # ── Also write CSV if requested ────────────────────────────────
    if csv_path:
        out = Path(csv_path)
        with out.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(deduped)
        print(f"✓ CSV:    {len(deduped)} rows → {out.resolve()}")

    # ── Coverage stats ─────────────────────────────────────────────
    total = len(deduped) or 1
    fields = ['funding_amount', 'round_stage', 'industry', 'country', 'investors']
    if enrich:
        fields.append('company_website')
    print(f"\nExtraction coverage:")
    for field in fields:
        count = sum(1 for e in deduped if e.get(field))
        label = field.replace('_', ' ').title()
        print(f"  • {label:<17} {count:>3}/{total}")

    return deduped


def main():
    parser = argparse.ArgumentParser(description='Fetch and extract funding news.')
    parser.add_argument('--days', type=int, default=7,
                        help='How many days back to fetch (default: 7)')
    parser.add_argument('--csv', type=str, default=None,
                        help='Also export a CSV to this path (optional)')
    parser.add_argument('--enrich', action='store_true',
                        help='Fetch each article to extract the company website')
    parser.add_argument('--workers', type=int, default=8,
                        help='Parallel workers for --enrich (default: 8)')
    args = parser.parse_args()

    try:
        run(days_back=args.days, csv_path=args.csv,
            enrich=args.enrich, workers=args.workers)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)


if __name__ == '__main__':
    main()
