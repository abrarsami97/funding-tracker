"""
FastAPI backend for the Funding Tracker dashboard.

Run:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

Endpoints:
    GET /                 -> serves frontend/index.html
    GET /api/data         -> filtered deals
    GET /api/meta         -> filter options derived from data
    GET /static/*         -> frontend assets

Database expected at funding_tracker.db (override with FUNDING_DB env var).
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Paths & config ────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = Path(os.environ.get("FUNDING_DB", ROOT / "funding_tracker.db"))
FRONTEND_DIR = ROOT / "frontend"

# Fixed FX (per spec). Adjust here when you switch to live rates.
FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012}

REFRESH_INTERVAL_HOURS = 12

# ── App init ──────────────────────────────────────────────────────────────
app = FastAPI(title="Funding Tracker API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend assets (styles.css, app.jsx, mock-data.js, etc.) at /static
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── DB helpers ────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(500, f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_deal(row: sqlite3.Row) -> dict:
    d = dict(row)
    amt = d.get("funding_amount") or 0.0
    cur = d.get("currency") or "USD"
    d["amount_usd"] = amt * FX.get(cur, 1.0)
    return d


def parse_list(s: Optional[str]) -> Optional[list[str]]:
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def industry_matches(deal_industry: Optional[str], wanted: list[str]) -> bool:
    if not deal_industry:
        return False
    tags = [t.strip() for t in deal_industry.split(",")]
    return any(w in tags for w in wanted)


def compute_refresh_marks() -> tuple[str, str]:
    """Last/next pipeline run timestamps (assumes 06:00 and 18:00 UTC daily)."""
    now = datetime.now(timezone.utc)
    if now.hour >= 18:
        last = now.replace(hour=18, minute=0, second=0, microsecond=0)
    elif now.hour >= 6:
        last = now.replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        last = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    nxt = last + timedelta(hours=REFRESH_INTERVAL_HOURS)
    return last.isoformat(), nxt.isoformat()


# ── Routes ────────────────────────────────────────────────────────────────
@app.get("/")
def root() -> FileResponse:
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(500, "frontend/index.html missing")
    return FileResponse(index)


@app.get("/api/data")
def get_data(
    days: int = Query(7, ge=1, le=365),
    industries: Optional[str] = None,
    countries: Optional[str] = None,
    min_amount_usd: Optional[float] = None,
    sources: Optional[str] = None,
):
    industries_list = parse_list(industries)
    countries_list = parse_list(countries)
    sources_list = parse_list(sources)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]

        # Date + country + source filtering can be done in SQL.
        sql = "SELECT * FROM deals WHERE published_date >= ?"
        args: list = [cutoff]

        if countries_list:
            sql += " AND country IN ({})".format(",".join("?" * len(countries_list)))
            args += countries_list

        if sources_list:
            sql += " AND source IN ({})".format(",".join("?" * len(sources_list)))
            args += sources_list

        sql += " ORDER BY published_date DESC"
        rows = conn.execute(sql, args).fetchall()

    deals = [row_to_deal(r) for r in rows]

    # Industry + min_amount filters in Python (industry is comma-joined text).
    if industries_list:
        deals = [d for d in deals if industry_matches(d.get("industry"), industries_list)]
    if min_amount_usd:
        deals = [d for d in deals if d["amount_usd"] >= min_amount_usd]

    last_updated, next_refresh = compute_refresh_marks()
    return {
        "deals": deals,
        "total": total,
        "filtered": len(deals),
        "last_updated": last_updated,
        "next_refresh": next_refresh,
    }


@app.get("/api/meta")
def get_meta():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT industry, country, source, round_stage FROM deals"
        ).fetchall()

    industries, countries, sources, rounds = set(), set(), set(), set()
    for r in rows:
        if r["industry"]:
            for t in r["industry"].split(","):
                t = t.strip()
                if t:
                    industries.add(t)
        if r["country"]:
            countries.add(r["country"])
        if r["source"]:
            sources.add(r["source"])
        if r["round_stage"]:
            rounds.add(r["round_stage"])

    return {
        "industries": sorted(industries),
        "countries": sorted(countries),
        "sources": sorted(sources),
        "rounds": sorted(rounds),
    }


@app.get("/api/health")
def health():
    return {"ok": True, "db": DB_PATH.exists(), "db_path": str(DB_PATH)}
