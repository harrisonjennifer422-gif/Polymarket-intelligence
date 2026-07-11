"""
SQLite storage for scan history and flagged mispricings.

SQLite was chosen deliberately for v1: this is a periodic-polling research
tool run by one person, not a high-concurrency service. A single file,
zero server setup, and it's still trivially queryable with any SQL client
or pandas. Migrate to Postgres/Timescale later if you scale to
tick-level data across many users.
"""

import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

from config import DB_PATH

# Ensure the directory for the DB file exists. Git doesn't track empty
# folders, so a fresh clone/deploy (like on Railway) can be missing
# data/ entirely even though it existed locally. Creating it at import
# time means this never depends on the deploy environment having it
# pre-made.
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    events_scanned INTEGER,
    poly_markets_scanned INTEGER,
    kalshi_markets_scanned INTEGER
);

CREATE TABLE IF NOT EXISTS arbitrage_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    event_id TEXT,
    event_title TEXT,
    outcome_sum REAL,
    deviation REAL,
    num_outcomes INTEGER,
    min_liquidity REAL,
    flagged_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES scan_runs(id)
);

CREATE TABLE IF NOT EXISTS cross_platform_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    poly_market_id TEXT,
    poly_question TEXT,
    kalshi_ticker TEXT,
    kalshi_title TEXT,
    similarity REAL,
    poly_prob REAL,
    kalshi_prob REAL,
    deviation REAL,
    flagged_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES scan_runs(id)
);

CREATE TABLE IF NOT EXISTS wallet_candidates (
    proxy_wallet TEXT PRIMARY KEY,
    username TEXT,
    rank TEXT,
    pnl REAL,
    vol REAL,
    trade_count INTEGER,
    wallet_age_days REAL,
    pnl_per_trade REAL,
    first_seen_run_id INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_run_id INTEGER,
    last_seen_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
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
        cur = conn.execute(
            "INSERT INTO scan_runs (started_at) VALUES (?)",
            (_now(),),
        )
        return cur.lastrowid


def finish_run(run_id: int, events_scanned: int, poly_count: int, kalshi_count: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE scan_runs
               SET finished_at=?, events_scanned=?, poly_markets_scanned=?,
                   kalshi_markets_scanned=?
               WHERE id=?""",
            (_now(), events_scanned, poly_count, kalshi_count, run_id),
        )


def insert_arbitrage_flag(run_id, event_id, event_title, outcome_sum,
                           deviation, num_outcomes, min_liquidity):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO arbitrage_flags
               (run_id, event_id, event_title, outcome_sum, deviation,
                num_outcomes, min_liquidity, flagged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, event_id, event_title, outcome_sum, deviation,
             num_outcomes, min_liquidity, _now()),
        )


def insert_cross_platform_flag(run_id, poly_market_id, poly_question,
                                kalshi_ticker, kalshi_title, similarity,
                                poly_prob, kalshi_prob, deviation):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cross_platform_flags
               (run_id, poly_market_id, poly_question, kalshi_ticker,
                kalshi_title, similarity, poly_prob, kalshi_prob,
                deviation, flagged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, poly_market_id, poly_question, kalshi_ticker,
             kalshi_title, similarity, poly_prob, kalshi_prob,
             deviation, _now()),
        )


def upsert_wallet_candidate(run_id, candidate: dict) -> bool:
    """
    Insert or update a wallet candidate. Returns True if this wallet is
    being seen for the first time (i.e. newly qualified) - used to decide
    whether to send a Discord alert, so you don't get re-alerted on the
    same wallet every single scan cycle.
    """
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT proxy_wallet FROM wallet_candidates WHERE proxy_wallet = ?",
            (candidate["proxy_wallet"],),
        ).fetchone()

        is_new = existing is None
        now = _now()

        if is_new:
            conn.execute(
                """INSERT INTO wallet_candidates
                   (proxy_wallet, username, rank, pnl, vol, trade_count,
                    wallet_age_days, pnl_per_trade, first_seen_run_id,
                    first_seen_at, last_seen_run_id, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate["proxy_wallet"], candidate["username"], candidate["rank"],
                 candidate["pnl"], candidate["vol"], candidate["trade_count"],
                 candidate["wallet_age_days"], candidate["pnl_per_trade"],
                 run_id, now, run_id, now),
            )
        else:
            conn.execute(
                """UPDATE wallet_candidates
                   SET username=?, rank=?, pnl=?, vol=?, trade_count=?,
                       wallet_age_days=?, pnl_per_trade=?, last_seen_run_id=?,
                       last_seen_at=?
                   WHERE proxy_wallet=?""",
                (candidate["username"], candidate["rank"], candidate["pnl"],
                 candidate["vol"], candidate["trade_count"], candidate["wallet_age_days"],
                 candidate["pnl_per_trade"], run_id, now, candidate["proxy_wallet"]),
            )

        return is_new


def _now():
    return datetime.now(timezone.utc).isoformat()
