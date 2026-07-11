"""
Polymarket Data API client (public, no auth) - data-api.polymarket.com

Used for wallet-level research: leaderboard discovery and per-wallet trade
history. This is a separate host/API from Gamma (market metadata) and CLOB
(order books) - it's the one that exposes on-chain positions, trades, and
activity keyed by wallet address.
"""

from config import DATA_API_BASE
from http_utils import get_json


def fetch_leaderboard(pool_size: int, category: str = "OVERALL",
                       time_period: str = "ALL", order_by: str = "PNL"):
    """
    Pull top traders from the leaderboard. Max 50 per page, max offset 1000
    (per the documented API limits), so pool_size is capped accordingly.

    Returns a list of dicts: rank, proxy_wallet, username, vol, pnl,
    verified_badge.
    """
    pool_size = min(pool_size, 1050)  # API's documented ceiling (offset max 1000 + limit 50)
    entries = []
    offset = 0
    page_size = 50  # API's documented max per page

    while len(entries) < pool_size:
        params = {
            "category": category,
            "timePeriod": time_period,
            "orderBy": order_by,
            "limit": min(page_size, pool_size - len(entries)),
            "offset": offset,
        }
        page = get_json(f"{DATA_API_BASE}/v1/leaderboard", params=params)

        if not page:
            break

        for entry in page:
            entries.append({
                "rank": entry.get("rank"),
                "proxy_wallet": entry.get("proxyWallet"),
                "username": entry.get("userName"),
                "vol": _to_float(entry.get("vol")),
                "pnl": _to_float(entry.get("pnl")),
                "verified_badge": bool(entry.get("verifiedBadge", False)),
            })

        offset += page_size
        if len(page) < page_size:
            break  # last page

    return entries


def fetch_wallet_trade_summary(proxy_wallet: str, max_trades: int):
    """
    Pull up to `max_trades` TRADE activity records for a wallet, sorted
    oldest-first. This single call serves double duty:

    1. If the wallet has fewer than max_trades total trades, this returns
       ALL of them - giving us both the exact trade count AND the earliest
       trade timestamp (= wallet age) in one request.
    2. If it returns exactly max_trades, we know the wallet has AT LEAST
       that many trades and can disqualify it immediately without paginating
       further - this is deliberately cheap for wallets that don't qualify.

    Returns: {"trade_count": int, "hit_cap": bool, "first_trade_ts": int|None,
              "last_trade_ts": int|None}
    """
    params = {
        "user": proxy_wallet,
        "type": "TRADE",
        "limit": max_trades,
        "sortBy": "TIMESTAMP",
        "sortDirection": "ASC",  # oldest first, so result[0] = earliest trade
    }
    activity = get_json(f"{DATA_API_BASE}/activity", params=params)

    if not activity:
        return {"trade_count": 0, "hit_cap": False, "first_trade_ts": None, "last_trade_ts": None}

    hit_cap = len(activity) >= max_trades
    return {
        "trade_count": len(activity),
        "hit_cap": hit_cap,
        "first_trade_ts": activity[0].get("timestamp"),
        "last_trade_ts": activity[-1].get("timestamp"),
    }


def fetch_wallet_activity_detailed(proxy_wallet: str, limit: int = 500):
    """
    Pull the FULL trade activity list for a wallet (not just the summary) -
    used for the deeper research dossier (behavioral pattern, events traded,
    buy/sell mix, trade sizing). Only called for wallets that already passed
    the cheap qualifying filter, since this is a heavier call.

    Returns a list of dicts: side, size, price, timestamp, title, eventSlug, outcome.
    """
    params = {
        "user": proxy_wallet,
        "type": "TRADE",
        "limit": limit,
        "sortBy": "TIMESTAMP",
        "sortDirection": "ASC",
    }
    activity = get_json(f"{DATA_API_BASE}/activity", params=params)
    if not activity:
        return []

    return [
        {
            "side": a.get("side"),
            "size": _to_float(a.get("size")),
            "price": _to_float(a.get("price")),
            "timestamp": a.get("timestamp"),
            "title": a.get("title"),
            "event_slug": a.get("eventSlug"),
            "outcome": a.get("outcome"),
        }
        for a in activity
    ]


def fetch_wallet_closed_positions(proxy_wallet: str, limit: int = 500):
    """
    Pull RESOLVED (closed) positions for a wallet - this is the real source
    of win/loss data, since each closed position carries a realizedPnl:
    positive = the position resolved as a win, negative/zero = a loss.

    Returns a list of dicts: title, event_slug, outcome, realized_pnl,
    avg_price, total_bought, end_date.
    """
    params = {
        "user": proxy_wallet,
        "limit": limit,
        "sortBy": "REALIZEDPNL",
        "sortDirection": "DESC",
    }
    positions = get_json(f"{DATA_API_BASE}/closed-positions", params=params)
    if not positions:
        return []

    return [
        {
            "title": p.get("title"),
            "event_slug": p.get("eventSlug"),
            "outcome": p.get("outcome"),
            "realized_pnl": _to_float(p.get("realizedPnl")),
            "avg_price": _to_float(p.get("avgPrice")),
            "total_bought": _to_float(p.get("totalBought")),
            "end_date": p.get("endDate"),
        }
        for p in positions
    ]


def fetch_wallet_open_positions(proxy_wallet: str, limit: int = 500):
    """
    Pull CURRENT open positions for a wallet - used to check whether a
    profitable-looking wallet is still actually active and what their
    live exposure looks like right now.

    Returns a list of dicts: title, event_slug, outcome, size, avg_price,
    cur_price, current_value, cash_pnl.
    """
    params = {
        "user": proxy_wallet,
        "limit": limit,
        "sortBy": "CURRENT",
        "sortDirection": "DESC",
    }
    positions = get_json(f"{DATA_API_BASE}/positions", params=params)
    if not positions:
        return []

    return [
        {
            "title": p.get("title"),
            "event_slug": p.get("eventSlug"),
            "outcome": p.get("outcome"),
            "size": _to_float(p.get("size")),
            "avg_price": _to_float(p.get("avgPrice")),
            "cur_price": _to_float(p.get("curPrice")),
            "current_value": _to_float(p.get("currentValue")),
            "cash_pnl": _to_float(p.get("cashPnl")),
        }
        for p in positions
    ]


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
