# Funding Tracker — Backend v1.1

A zero-cost Python pipeline that pulls startup funding news from 8 RSS sources,
extracts structured data (company, amount, round, industry, country, investors,
optionally the company website), deduplicates, and exports to CSV.

No LLM API calls. No paid APIs. Runs anywhere Python runs.

## What's new in v1.1

Three new columns: `country`, `investors`, `company_website`.

- `country` and `investors` come from the same RSS data — free, no extra I/O.
- `company_website` requires fetching each article's full HTML, so it's
  behind a `--enrich` flag and cached so reruns don't refetch.

## Project structure

```
funding-tracker/
├── fetchers/
│   ├── base.py              # Shared RSS-fetch logic — all fetchers inherit
│   ├── techcrunch.py        # One file per source
│   ├── vcnewsdaily.py
│   ├── techfundingnews.py
│   ├── techstartups.py
│   ├── finsmes.py
│   ├── sifted.py
│   ├── inc42.py
│   └── eustartups.py
├── extractor.py             # Regex extraction (amount, round, company, industry,
│                            #                    country, investors)
├── enricher.py              # Optional per-article fetch for company website
├── main.py                  # Orchestrator — run this
├── requirements.txt
└── README.md
```

## Setup (local — Windows/Mac/Linux)

You need Python 3.9+. Check with `python --version`.

```bash
# 1. Open a terminal in this folder
cd funding-tracker

# 2. (Recommended) Create a virtual environment
python -m venv .venv

# 3. Activate it
#    macOS/Linux:
source .venv/bin/activate
#    Windows (PowerShell):
.venv\Scripts\Activate.ps1
#    Windows (Command Prompt):
.venv\Scripts\activate.bat

# 4. Install dependencies
pip install -r requirements.txt
```

## Run

```bash
# Default: 7 days, no website enrichment, saves to funding_news.csv
python main.py

# Same but with company-website enrichment (fetches each article, ~30s)
python main.py --enrich

# Last 14 days
python main.py --days 14

# Custom output filename
python main.py --out today.csv

# More parallelism for enrichment
python main.py --enrich --workers 12
```

Sample output:

```
Funding tracker — pulling articles since 2026-05-10
(7 days back)

  → TechCrunch              42 entries
  → VC News Daily           87 entries
  → Tech Funding News       29 entries
  → Tech Startups           18 entries
  → FinSMEs                 51 entries
  → Sifted                  34 entries
  → Inc42                   45 entries
  → EU-Startups             28 entries

Raw entries collected: 334
After funding-article filter: 198
After URL dedup: 172

Enriching with company websites (8 workers)...
  Cache hits: 0, misses: 172
  ... enriched 20/172
  ... enriched 40/172
  ...
  Websites found: 118/172

✓ Wrote 172 rows to /Users/.../funding_news.csv

Extraction coverage:
  • Funding Amount    147/172
  • Round Stage       121/172
  • Industry          138/172
  • Country           134/172
  • Investors         108/172
  • Company Website   118/172
```

## CSV columns

| Column | Type | Example |
|---|---|---|
| published_date | ISO datetime | `2026-05-15T14:32:00+00:00` |
| source | string | `TechCrunch` |
| company_name | string | `Anthropic` |
| funding_amount | number | `5000000000` (native currency units) |
| currency | string | `USD`, `EUR`, `GBP`, `INR` |
| round_stage | string | `Series A`, `Seed`, `Pre-Seed` |
| industry | string | `AI/ML, Fintech` (comma-separated, up to 2) |
| **country** | string | `United States`, `Germany`, `India` |
| **investors** | string | `Accel, Sequoia, Lightspeed` (comma-separated, up to 3) |
| **company_website** | string | `https://anthropic.com` (only with `--enrich`) |
| title | string | original headline |
| url | string | link to article |
| summary | string | cleaned article preview text |

`funding_amount` is stored in the native currency's base unit (no scaling).
For €15m the row is `15000000` with currency `EUR`. Keeps raw values so you
can re-do FX conversions later.

## How extraction works

Four regex stages plus an optional fetch:

1. **Filter** — Articles must contain at least one funding signal word
   (`raise`, `funding`, `series`, `seed round`, etc.).
2. **Extract** — Patterns for amount (currency + number + unit), round
   (`Series [A-K]`, `Seed`...), and company name (entity before the funding verb).
3. **Classify** — Industry from a keyword taxonomy.
4. **Locate** — Country via a 5-step ladder:
   - "X-based" / "based in X" pattern
   - Bare city name in first 200 chars (Berlin → Germany)
   - Demonym ("Indian startup", "British fintech")
   - Country name in first 200 chars
   - Source fallback (Inc42 → India)
5. **Investors** — Capture phrasing after `led by`, `backed by`,
   `with participation from`, `investors include`, etc.
6. **Website** *(only with `--enrich`)* — Fetch each article URL, find
   outbound links, rank by company-name match in anchor/domain + position
   + URL shape. Cached to `.enrichment_cache.json`.

Expected coverage: ~85% amount/round, ~75% country, ~70% investors,
~60% website (when enrichment runs).

## Adding a new source

Create a new file in `fetchers/`:

```python
# fetchers/yourstory.py
from .base import BaseFetcher

class YourStoryFetcher(BaseFetcher):
    name = "YourStory"
    feed_url = "https://yourstory.com/feed"
```

Then register it in `main.py`:

```python
from fetchers.yourstory import YourStoryFetcher
FETCHERS = [
    # ... existing
    YourStoryFetcher(),
]
```

## Tuning the extractor

All extraction rules live in `extractor.py`:

- `_AMOUNT_PATTERNS` — currency formats
- `_ROUND_RE` — round name patterns
- `_FUNDING_VERBS` — verbs that follow company names
- `INDUSTRY_KEYWORDS` — industry taxonomy
- `_CITY_TO_COUNTRY`, `_DEMONYM_TO_COUNTRY` — country resolution
- `_INVESTOR_TRIGGERS` — phrases that precede investor names
- `_INVESTOR_STOP_PATTERN` — where the investor list ends

Quick test:

```bash
python -c "from extractor import extract; print(extract({'source': 'Inc42', 'title': 'YOUR TEST TITLE', 'summary': 'YOUR SUMMARY'}))"
```

## Enrichment cache

`--enrich` writes `.enrichment_cache.json` in the working directory. It maps
`article_url → company_website_or_None` so re-runs skip articles already
seen. Delete this file to force a clean refetch.

## Roadmap

1. **v1 (this):** RSS → CSV with country, investors, optional website. ✓
2. **v2:** SQLite storage so we accumulate history and dedupe across runs.
3. **v3:** SEC EDGAR Form D fetcher (structured XML, no LLM needed).
4. **v4:** LLM fallback — only when regex returns null on a high-confidence
   funding article. Keeps cost near zero.
5. **v5:** Flask/FastAPI dashboard on a VPS, cron'd every 12 hours.

## Troubleshooting

**"FAILED (URLError: ...)"** — one of the RSS feeds is down. The others
continue. Re-run later.

**"ImportError: No module named feedparser"** — venv not activated, or
deps not installed.

**Enrichment finds no websites** — many publishers strip outbound links from
their content. Try `--workers 4` if you're getting timeouts (slower but more
reliable). Check `.enrichment_cache.json` to see what was returned per URL.

**Low extraction coverage on a field** — share a few example titles that
didn't extract and tune the regex in `extractor.py`.
