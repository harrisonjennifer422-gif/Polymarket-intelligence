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


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
