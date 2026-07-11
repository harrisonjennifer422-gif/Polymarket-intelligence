"""
Central configuration for the Polymarket Mispricing Scanner.
All thresholds and API endpoints live here so you can tune the system
without touching logic code.
"""

# ---- API base URLs (public, no auth required for reads) ----
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
# NOTE: Kalshi has published more than one base host across its own docs/mirrors
# (e.g. external-api.kalshi.com). If this one starts 404ing, check
# https://docs.kalshi.com for the current production host before assuming
# the scanner is broken.

# ---- Mispricing thresholds ----
# Your "edge threshold" in percentage points (0.05 = 5pp, 0.10 = 10pp)
EDGE_THRESHOLD_LOW = 0.05
EDGE_THRESHOLD_HIGH = 0.10

# Minimum liquidity (USD) for a market to be worth flagging.
# Illiquid markets show huge "mispricings" that are just noise / unfillable.
MIN_LIQUIDITY_USD = 1000

# Minimum 24h volume (USD) - same reasoning as above.
MIN_VOLUME_24H_USD = 500

# ---- Cross-platform matching ----
# Minimum title-similarity score (0-1) before we treat two markets as
# "the same real-world event" across platforms. This is a heuristic gate,
# not a guarantee - always eyeball a new match before trusting it.
TITLE_MATCH_THRESHOLD = 0.55

# ---- Networking ----
REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5  # exponential backoff base

# ---- Storage ----
DB_PATH = "data/mispricing.db"

# ---- Discord alerts ----
import os

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
# Optional separate channel for wallet-candidate alerts. Falls back to the
# main webhook if unset, so this is opt-in, not required.
DISCORD_WALLET_WEBHOOK_URL = os.environ.get("DISCORD_WALLET_WEBHOOK_URL", "") or DISCORD_WEBHOOK_URL
# Only alert on flags at/above this deviation, separate from the storage
# threshold above - keeps Discord from getting spammed by every 5pp flag
# while you still keep all of them in the DB for research.
DISCORD_ALERT_MIN_DEVIATION = float(os.environ.get("DISCORD_ALERT_MIN_DEVIATION", "0.07"))
# Cap how many alerts get sent per run so a bad scan doesn't flood the channel
DISCORD_MAX_ALERTS_PER_RUN = int(os.environ.get("DISCORD_MAX_ALERTS_PER_RUN", "8"))

# ---- Scheduling (for Railway "worker" deployment) ----
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "900"))  # 15 min default
MAX_KALSHI_PER_SCAN = int(os.environ.get("MAX_KALSHI_PER_SCAN", "500"))

# ---- Market category coverage ----
# Real Polymarket Gamma tag IDs (confirmed against Polymarket's own
# open-source frontend code). Without tag filtering, a generic "all active
# events" pull tends to get dominated by whatever's most active right now
# (e.g. 2028 election markets) - explicit per-category pulls guarantee
# crypto, geopolitics, etc. are always represented, not just whatever is
# trending hardest this week.
CATEGORY_TAG_IDS = {
    "politics": 2,
    "crypto": 21,
    "geopolitics": 100265,
    "finance": 120,
    "sports": 100639,
    "tech": 1401,
    "culture": 596,
}

# Which categories to actually scan each cycle. Comma-separated env var,
# e.g. "politics,crypto,geopolitics,finance"
_default_categories = "politics,crypto,geopolitics,finance,tech,culture"
CATEGORIES_TO_SCAN = [
    c.strip() for c in os.environ.get("CATEGORIES_TO_SCAN", _default_categories).split(",")
    if c.strip()
]

# Max events pulled PER CATEGORY per scan (not total) - this is what
# guarantees crypto/geopolitics/etc. get real representation instead of
# being crowded out by whatever's trending.
MAX_EVENTS_PER_CATEGORY = int(os.environ.get("MAX_EVENTS_PER_CATEGORY", "60"))

# ---- Wallet scoring / copy-trading candidates ----
DATA_API_BASE = "https://data-api.polymarket.com"

# How many top-leaderboard wallets to examine as candidates per wallet-scan cycle.
# Each candidate costs one extra API call, so keep this reasonable.
WALLET_LEADERBOARD_POOL_SIZE = int(os.environ.get("WALLET_LEADERBOARD_POOL_SIZE", "100"))

# "At least 3-6 months old" - minimum wallet age in days before it qualifies.
# Default is 90 (3 months). Raise to 180 for a stricter 6-month floor.
WALLET_MIN_AGE_DAYS = int(os.environ.get("WALLET_MIN_AGE_DAYS", "90"))

# "Little trade entries" - wallets with more total trades than this are treated
# as high-frequency/bot-like and skipped. This is the core filter that separates
# "selective, high-conviction trader" from "prolific trader/bot."
WALLET_MAX_TRADE_COUNT = int(os.environ.get("WALLET_MAX_TRADE_COUNT", "150"))

# Minimum all-time PnL (USD) to bother considering - filters out wallets that
# are "profitable" by a trivial amount.
WALLET_MIN_PNL_USD = float(os.environ.get("WALLET_MIN_PNL_USD", "2000"))

# Wallet scans are more expensive (1 extra API call per candidate) than the
# mispricing scans, so run them less often - every Nth main-scan cycle.
WALLET_SCAN_EVERY_N_RUNS = int(os.environ.get("WALLET_SCAN_EVERY_N_RUNS", "4"))
