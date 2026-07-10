"""
Kalshi data client - public market-data endpoints (no auth required).

Used purely as an independent, real external price reference for the
cross-platform mispricing scanner. We never place orders here.
"""

from config import KALSHI_API_BASE
from http_utils import get_json


def fetch_open_markets(max_markets: int = 500, page_size: int = 100):
    """
    Pull open Kalshi markets. Returns a normalized list.
    """
    markets = []
    cursor = None

    while len(markets) < max_markets:
        params = {"status": "open", "limit": min(page_size, max_markets - len(markets))}
        if cursor:
            params["cursor"] = cursor

        data = get_json(f"{KALSHI_API_BASE}/markets", params=params)
        page_markets = data.get("markets", [])
        cursor = data.get("cursor")

        if not page_markets:
            break

        for m in page_markets:
            markets.append(_normalize_market(m))

        if not cursor:
            break

    return markets


def _normalize_market(m: dict) -> dict:
    yes_bid = _to_prob(m.get("yes_bid"))
    yes_ask = _to_prob(m.get("yes_ask"))
    last_price = _to_prob(m.get("last_price"))

    # Mid price is a more stable "implied probability" than last trade,
    # which can be stale on thin markets.
    mid = None
    if yes_bid is not None and yes_ask is not None:
        mid = (yes_bid + yes_ask) / 2
    elif last_price is not None:
        mid = last_price

    return {
        "ticker": m.get("ticker"),
        "event_ticker": m.get("event_ticker"),
        "title": m.get("title") or m.get("subtitle"),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "last_price": last_price,
        "implied_prob": mid,
        "volume": _to_float(m.get("volume")),
        "open_interest": _to_float(m.get("open_interest")),
        "close_time": m.get("close_time"),
    }


def _to_prob(cents):
    """Kalshi quotes prices in cents (1-99). Convert to 0-1 probability."""
    try:
        return float(cents) / 100.0
    except (TypeError, ValueError):
        return None


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
