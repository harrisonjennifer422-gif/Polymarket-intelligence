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
# Only alert on flags at/above this deviation, separate from the storage
# threshold above - keeps Discord from getting spammed by every 5pp flag
# while you still keep all of them in the DB for research.
DISCORD_ALERT_MIN_DEVIATION = float(os.environ.get("DISCORD_ALERT_MIN_DEVIATION", "0.07"))
# Cap how many alerts get sent per run so a bad scan doesn't flood the channel
DISCORD_MAX_ALERTS_PER_RUN = int(os.environ.get("DISCORD_MAX_ALERTS_PER_RUN", "8"))

# ---- Scheduling (for Railway "worker" deployment) ----
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "900"))  # 15 min default
MAX_EVENTS_PER_SCAN = int(os.environ.get("MAX_EVENTS_PER_SCAN", "300"))
MAX_KALSHI_PER_SCAN = int(os.environ.get("MAX_KALSHI_PER_SCAN", "500"))
