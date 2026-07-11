"""
Wallet research dossier - the deeper analysis pass run ONLY on wallets that
already passed the cheap leaderboard filter (profitable, aged, low trade
count). This is where we answer: is this wallet actually good for
copy-trading, and why?

Everything here is derived from real Polymarket Data API data:
  - /closed-positions -> realizedPnl per resolved position = the actual
    win/loss record (positive realizedPnl = win, non-positive = loss)
  - /positions -> current open exposure (are they still active, and in what?)
  - /activity -> full trade list = behavioral pattern (frequency, buy/sell
    mix, which events they trade, trade sizing)

The "copytrade_fit" verdict is a transparent, rule-based heuristic - not a
guarantee of future performance. Past resolved trades are real outcomes,
but a small sample size can still be luck. The verdict always comes with
its reasoning spelled out so you can judge the reasoning yourself, not just
trust a boolean.
"""

from collections import Counter
from datetime import datetime, timezone

from polymarket_data_client import (
    fetch_wallet_activity_detailed,
    fetch_wallet_closed_positions,
    fetch_wallet_open_positions,
)

# Minimum resolved trades before we're willing to call a win rate meaningful.
# Below this, sample size is too small to say much - the verdict will say so.
MIN_RESOLVED_FOR_VERDICT = 3

# Win rate threshold for a positive copytrade verdict (heuristic, not a promise).
WIN_RATE_FIT_THRESHOLD = 0.55

# Minimum distinct events for "diversified" - avoids crowning a wallet that
# got lucky on one big binary bet as a "skilled" trader.
MIN_DISTINCT_EVENTS_FOR_FIT = 2


def build_dossier(candidate: dict) -> dict:
    """
    Given a candidate dict (from wallet_scoring.find_wallet_candidates),
    fetches the deeper data and returns the candidate merged with a full
    research dossier.
    """
    wallet = candidate["proxy_wallet"]

    activity = fetch_wallet_activity_detailed(wallet, limit=max(candidate["trade_count"], 1))
    closed_positions = fetch_wallet_closed_positions(wallet, limit=500)
    open_positions = fetch_wallet_open_positions(wallet, limit=500)

    win_loss = _compute_win_loss(closed_positions)
    behavior = _compute_behavior(activity, candidate["wallet_age_days"])
    exposure = _compute_open_exposure(open_positions)
    verdict = _compute_copytrade_verdict(win_loss, behavior)

    return {
        **candidate,
        **win_loss,
        **behavior,
        **exposure,
        **verdict,
    }


def _compute_win_loss(closed_positions: list) -> dict:
    if not closed_positions:
        return {
            "wins": 0, "losses": 0, "win_rate": None,
            "avg_win": 0.0, "avg_loss": 0.0, "total_realized_pnl": 0.0,
            "resolved_count": 0,
        }

    wins = [p for p in closed_positions if p["realized_pnl"] > 0]
    losses = [p for p in closed_positions if p["realized_pnl"] <= 0]
    resolved_count = len(wins) + len(losses)

    return {
        "wins": len(wins),
        "losses": len(losses),
        "resolved_count": resolved_count,
        "win_rate": round(len(wins) / resolved_count, 3) if resolved_count else None,
        "avg_win": round(sum(p["realized_pnl"] for p in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(p["realized_pnl"] for p in losses) / len(losses), 2) if losses else 0.0,
        "total_realized_pnl": round(sum(p["realized_pnl"] for p in closed_positions), 2),
    }


def _compute_behavior(activity: list, wallet_age_days: float) -> dict:
    if not activity:
        return {
            "trades_per_day": 0.0, "distinct_events": 0, "top_events": [],
            "buy_ratio": None, "avg_trade_size_usd": 0.0, "largest_trade_usd": 0.0,
            "behavioral_pattern": "Not enough trade data to characterize behavior.",
        }

    trades_per_day = round(len(activity) / max(wallet_age_days, 1), 3)

    event_counter = Counter(a["event_slug"] or a["title"] for a in activity if a.get("event_slug") or a.get("title"))
    distinct_events = len(event_counter)
    top_events = [
        {"event": ev, "trade_count": count}
        for ev, count in event_counter.most_common(5)
    ]

    buy_count = sum(1 for a in activity if a["side"] == "BUY")
    buy_ratio = round(buy_count / len(activity), 3)

    trade_sizes_usd = [a["size"] * a["price"] for a in activity if a["size"] and a["price"]]
    avg_trade_size_usd = round(sum(trade_sizes_usd) / len(trade_sizes_usd), 2) if trade_sizes_usd else 0.0
    largest_trade_usd = round(max(trade_sizes_usd), 2) if trade_sizes_usd else 0.0

    # Plain-language behavioral pattern description, built from the real
    # numbers above - no fabricated psychology, just what the data shows.
    concentration = (
        "concentrated in a small handful of events" if distinct_events <= 3
        else "diversified across many independent events"
    )
    if buy_ratio >= 0.8:
        directionality = "almost always opens new positions rather than exiting early"
    elif buy_ratio <= 0.2:
        directionality = "mostly exits/closes positions rather than opening fresh ones"
    else:
        directionality = "a balanced mix of opening and closing positions"

    behavioral_pattern = (
        f"Trades about {trades_per_day:.2f}x/day on average, {concentration} "
        f"({distinct_events} distinct events), with {directionality} "
        f"({buy_ratio*100:.0f}% of activity is BUY-side). Average trade size "
        f"is roughly ${avg_trade_size_usd:,.0f}, with a largest single trade "
        f"of ${largest_trade_usd:,.0f}."
    )

    return {
        "trades_per_day": trades_per_day,
        "distinct_events": distinct_events,
        "top_events": top_events,
        "buy_ratio": buy_ratio,
        "avg_trade_size_usd": avg_trade_size_usd,
        "largest_trade_usd": largest_trade_usd,
        "behavioral_pattern": behavioral_pattern,
    }


def _compute_open_exposure(open_positions: list) -> dict:
    return {
        "open_positions_count": len(open_positions),
        "open_exposure_usd": round(sum(p["current_value"] for p in open_positions), 2),
    }


def _compute_copytrade_verdict(win_loss: dict, behavior: dict) -> dict:
    resolved = win_loss["resolved_count"]
    win_rate = win_loss["win_rate"]
    distinct_events = behavior["distinct_events"]

    if resolved < MIN_RESOLVED_FOR_VERDICT:
        return {
            "copytrade_fit": False,
            "copytrade_reason": (
                f"Only {resolved} resolved trade(s) on record — too small a "
                f"sample to judge a real win rate yet. Worth watching, not "
                f"worth copying yet."
            ),
        }

    if win_rate is not None and win_rate >= WIN_RATE_FIT_THRESHOLD and distinct_events >= MIN_DISTINCT_EVENTS_FOR_FIT:
        return {
            "copytrade_fit": True,
            "copytrade_reason": (
                f"{win_rate*100:.0f}% win rate across {resolved} resolved trades "
                f"spanning {distinct_events} distinct events — profits look "
                f"spread across multiple independent bets rather than one "
                f"lucky call. Reasonable copy-trading research candidate."
            ),
        }

    if distinct_events < MIN_DISTINCT_EVENTS_FOR_FIT:
        return {
            "copytrade_fit": False,
            "copytrade_reason": (
                f"Profits are concentrated in only {distinct_events} event(s) — "
                f"this could be one lucky binary call rather than a repeatable "
                f"edge. Needs more resolved history across different events "
                f"before it's a reasonable copy-trading candidate."
            ),
        }

    return {
        "copytrade_fit": False,
        "copytrade_reason": (
            f"{win_rate*100:.0f}% win rate across {resolved} resolved trades is "
            f"below the {WIN_RATE_FIT_THRESHOLD*100:.0f}% bar this scanner uses "
            f"as a rough quality filter — high all-time PnL here may be driven "
            f"by a few large wins offsetting many losses, which is a riskier "
            f"profile to copy."
        ),
    }
