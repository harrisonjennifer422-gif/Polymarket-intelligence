"""
Wallet scoring for copy-trading research.

The goal (per your brief): find wallets that are
  - genuinely profitable (meaningful all-time PnL, not a rounding error)
  - at least 3-6 months old (survived multiple market cycles, not a
    brand-new lucky account)
  - low trade count ("little trade entries") - selective, high-conviction
    traders rather than high-frequency bots or overtraders

This is a candidate list for research, not an auto-follow list. Past PnL on
a public leaderboard is not proof of a repeatable edge - a wallet can be
profitable from one lucky binary outcome. The research layer below exists
specifically to force that check before you treat anyone as "worth copying."
"""

from datetime import datetime, timezone

from config import (
    WALLET_LEADERBOARD_POOL_SIZE,
    WALLET_MIN_AGE_DAYS,
    WALLET_MAX_TRADE_COUNT,
    WALLET_MIN_PNL_USD,
)
from polymarket_data_client import fetch_leaderboard, fetch_wallet_trade_summary


def find_wallet_candidates():
    """
    Returns a list of qualifying wallet dicts:
    proxy_wallet, username, pnl, vol, trade_count, wallet_age_days,
    pnl_per_trade, rank.
    """
    leaderboard = fetch_leaderboard(
        pool_size=WALLET_LEADERBOARD_POOL_SIZE,
        category="OVERALL",
        time_period="ALL",
        order_by="PNL",
    )

    candidates = []
    now = datetime.now(timezone.utc).timestamp()

    for entry in leaderboard:
        if entry["pnl"] < WALLET_MIN_PNL_USD:
            continue  # leaderboard is sorted by PNL desc, but don't assume - check explicitly

        wallet = entry["proxy_wallet"]
        if not wallet:
            continue

        summary = fetch_wallet_trade_summary(wallet, max_trades=WALLET_MAX_TRADE_COUNT)

        # Disqualify high-frequency wallets immediately - we deliberately
        # don't paginate further to confirm the exact count, since "too many
        # trades" already fails the "little trade entries" requirement.
        if summary["hit_cap"]:
            continue

        if summary["trade_count"] == 0 or summary["first_trade_ts"] is None:
            continue

        wallet_age_days = (now - summary["first_trade_ts"]) / 86400

        if wallet_age_days < WALLET_MIN_AGE_DAYS:
            continue

        pnl_per_trade = entry["pnl"] / summary["trade_count"] if summary["trade_count"] else 0

        candidates.append({
            "proxy_wallet": wallet,
            "username": entry["username"],
            "rank": entry["rank"],
            "pnl": round(entry["pnl"], 2),
            "vol": round(entry["vol"], 2),
            "trade_count": summary["trade_count"],
            "wallet_age_days": round(wallet_age_days, 1),
            "pnl_per_trade": round(pnl_per_trade, 2),
        })

    # Highest pnl-per-trade first - proxy for "selective and effective"
    # rather than just "big volume"
    candidates.sort(key=lambda c: -c["pnl_per_trade"])
    return candidates
