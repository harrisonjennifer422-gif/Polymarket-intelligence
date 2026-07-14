"""
SQLite storage for the full pipeline. Same engine as v1 (single file,
zero server setup - appropriate for a periodic-polling research tool),
extended schema to store every object in storage/schemas.py.

All records beyond the simplest ones are stored as JSON blobs in a
generic `records` table with a `record_type` discriminator - this avoids
hand-writing 9 separate CREATE TABLE statements that all need to evolve
in lockstep with schemas.py, at the cost of losing per-field SQL queries
(fine for a research tool; query by record_type +市 filter in Python).
Wallet candidates keep their own richer table since we query/sort on
specific columns (win_rate, copytrade score) often enough to want real columns.
"""

from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="storage.db",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure local computation over already-fetched data, no external calls of any kind.",
))

import sqlite3
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from config.loader import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    events_scanned INTEGER,
    poly_markets_scanned INTEGER,
    kalshi_markets_scanned INTEGER
);

-- Generic JSON-blob store for MarketSnapshot, VerificationRecord,
-- HistoricalEventRecord, MispricingSignal, MarketIntelligenceReport,
-- DiscordAlertPayload, TradeFill, WalletFeatureVector.
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    record_type TEXT NOT NULL,
    market_id TEXT,
    wallet_address TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_type ON records(record_type);
CREATE INDEX IF NOT EXISTS idx_records_market ON records(market_id);

-- Verification cache: avoid re-paying for the same market's evidence
-- check every scan cycle.
CREATE TABLE IF NOT EXISTS verification_cache (
    market_id TEXT PRIMARY KEY,
    verification_json TEXT NOT NULL,
    cached_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_candidates (
    wallet_address TEXT PRIMARY KEY,
    username TEXT,
    rank TEXT,
    pnl REAL,
    vol REAL,
    trade_count INTEGER,
    wallet_age_days REAL,
    pnl_per_trade REAL,
    wins INTEGER,
    losses INTEGER,
    resolved_count INTEGER,
    win_rate REAL,
    avg_win REAL,
    avg_loss REAL,
    total_realized_pnl REAL,
    trades_per_day REAL,
    distinct_events INTEGER,
    top_events TEXT,
    buy_ratio REAL,
    avg_trade_size_usd REAL,
    largest_trade_usd REAL,
    behavioral_pattern TEXT,
    open_positions_count INTEGER,
    open_exposure_usd REAL,
    behavior_label TEXT,
    copy_trade_score INTEGER,
    copy_trade_recommendation TEXT,
    why_copy_or_not TEXT,
    first_seen_run_id INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_run_id INTEGER,
    last_seen_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def start_run() -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO scan_runs (started_at) VALUES (?)", (_now(),))
        return cur.lastrowid


def finish_run(run_id: int, events_scanned: int, poly_count: int, kalshi_count: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE scan_runs SET finished_at=?, events_scanned=?, poly_markets_scanned=?,
               kalshi_markets_scanned=? WHERE id=?""",
            (_now(), events_scanned, poly_count, kalshi_count, run_id),
        )


def save_record(run_id, record_type: str, payload: dict, market_id: str = None, wallet_address: str = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO records (run_id, record_type, market_id, wallet_address, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, record_type, market_id, wallet_address, json.dumps(payload), _now()),
        )


def get_cached_verification(market_id: str, max_age_hours: float):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT verification_json, cached_at FROM verification_cache WHERE market_id=?",
            (market_id,),
        ).fetchone()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row["cached_at"])
    age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return json.loads(row["verification_json"])


def set_cached_verification(market_id: str, verification: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO verification_cache (market_id, verification_json, cached_at)
               VALUES (?, ?, ?)
               ON CONFLICT(market_id) DO UPDATE SET verification_json=excluded.verification_json,
                   cached_at=excluded.cached_at""",
            (market_id, json.dumps(verification), _now()),
        )


_WALLET_COLUMNS = [
    "wallet_address", "username", "rank", "pnl", "vol", "trade_count", "wallet_age_days",
    "pnl_per_trade", "wins", "losses", "resolved_count", "win_rate", "avg_win", "avg_loss",
    "total_realized_pnl", "trades_per_day", "distinct_events", "top_events", "buy_ratio",
    "avg_trade_size_usd", "largest_trade_usd", "behavioral_pattern", "open_positions_count",
    "open_exposure_usd", "behavior_label", "copy_trade_score", "copy_trade_recommendation",
    "why_copy_or_not",
]


def upsert_wallet_candidate(run_id, wallet: dict) -> bool:
    """Insert or update; returns True if newly discovered (drives Discord dedup)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT wallet_address FROM wallet_candidates WHERE wallet_address = ?",
            (wallet["wallet_address"],),
        ).fetchone()
        is_new = existing is None
        now = _now()

        values = {c: wallet.get(c) for c in _WALLET_COLUMNS}
        if "top_events" in values and isinstance(values["top_events"], list):
            values["top_events"] = json.dumps(values["top_events"])

        if is_new:
            cols = _WALLET_COLUMNS + ["first_seen_run_id", "first_seen_at", "last_seen_run_id", "last_seen_at"]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO wallet_candidates ({', '.join(cols)}) VALUES ({placeholders})",
                [values.get(c) for c in _WALLET_COLUMNS] + [run_id, now, run_id, now],
            )
        else:
            set_clause = ", ".join(f"{c}=?" for c in _WALLET_COLUMNS if c != "wallet_address")
            conn.execute(
                f"UPDATE wallet_candidates SET {set_clause}, last_seen_run_id=?, last_seen_at=? WHERE wallet_address=?",
                [values.get(c) for c in _WALLET_COLUMNS if c != "wallet_address"]
                + [run_id, now, wallet["wallet_address"]],
            )
        return is_new


def _now():
    return datetime.now(timezone.utc).isoformat()
