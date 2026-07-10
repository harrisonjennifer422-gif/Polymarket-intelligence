"""
Two independent mispricing detectors.

1. arbitrage_scan: pure Polymarket data. For "neg-risk" grouped events
   (e.g. "Who will win the election" split into many Yes/No markets,
   one per candidate), the Yes-prices across the group should sum to
   ~1.0. This requires no external model - it's a mechanical consistency
   check, and any deviation above your threshold is a real, storable
   edge (subject to liquidity/slippage - see caveats below).

2. cross_platform_scan: compares a Polymarket market's implied
   probability against an independently-matched Kalshi market on the
   same real-world event. This is your closest thing to an "external
   model" that isn't vibes - it's a second, independent market pricing
   the same outcome.

CAVEATS (read before trading on these flags):
- Liquidity/volume filters exist because thin markets show huge fake
  "mispricings" you can't actually capture without moving the price
  yourself.
- Cross-platform matches are similarity-based, not guaranteed - a market
  matched at 0.6 similarity might resolve on subtly different criteria
  (different deadline, different source of truth). Always check the
  actual resolution criteria before trusting a flagged deviation.
- Fees, spread, and settlement timing eat into any "edge" you compute
  from mid-prices. Treat these numbers as candidates for research, not
  as executable P&L.
"""

from config import (
    EDGE_THRESHOLD_LOW,
    MIN_LIQUIDITY_USD,
    MIN_VOLUME_24H_USD,
)
from matcher import find_matches


def arbitrage_scan(events: list) -> list:
    """
    Groups markets within each neg-risk event, sums Yes-outcome prices,
    and flags groups that deviate from 1.0 beyond EDGE_THRESHOLD_LOW.

    Returns list of flag dicts.
    """
    flags = []

    for event in events:
        neg_risk_markets = [m for m in event["markets"] if m.get("neg_risk")]
        if len(neg_risk_markets) < 2:
            continue  # arbitrage check needs multiple mutually-exclusive outcomes

        yes_prices = []
        min_liquidity = None
        for m in neg_risk_markets:
            yes_price = m["outcome_prices"].get("Yes")
            if yes_price is None:
                continue
            yes_prices.append(yes_price)
            liq = m.get("liquidity", 0)
            min_liquidity = liq if min_liquidity is None else min(min_liquidity, liq)

        if len(yes_prices) < 2:
            continue

        # Liquidity gate - skip groups where the thinnest market can't
        # actually absorb a trade.
        if min_liquidity is not None and min_liquidity < MIN_LIQUIDITY_USD:
            continue

        outcome_sum = sum(yes_prices)
        deviation = abs(outcome_sum - 1.0)

        if deviation >= EDGE_THRESHOLD_LOW:
            flags.append({
                "event_id": event["event_id"],
                "event_title": event["title"],
                "outcome_sum": round(outcome_sum, 4),
                "deviation": round(deviation, 4),
                "num_outcomes": len(yes_prices),
                "min_liquidity": min_liquidity,
            })

    return flags


def cross_platform_scan(poly_events: list, kalshi_markets: list) -> list:
    """
    Flattens Polymarket markets out of events, matches them against Kalshi
    markets by title similarity, and flags probability deviations above
    EDGE_THRESHOLD_LOW.

    Returns list of flag dicts.
    """
    poly_markets = []
    for event in poly_events:
        for m in event["markets"]:
            m["_event_title"] = event["title"]
            poly_markets.append(m)

    # Filter for liquidity/volume before even attempting matches - no
    # point flagging a deviation you can't trade.
    poly_markets = [
        m for m in poly_markets
        if m.get("liquidity", 0) >= MIN_LIQUIDITY_USD
        and m.get("volume_24h", 0) >= MIN_VOLUME_24H_USD
    ]

    matches = find_matches(poly_markets, kalshi_markets)

    flags = []
    for match in matches:
        pm = match["poly_market"]
        km = match["kalshi_market"]

        poly_prob = pm["outcome_prices"].get("Yes")
        kalshi_prob = km.get("implied_prob")

        if poly_prob is None or kalshi_prob is None:
            continue

        deviation = abs(poly_prob - kalshi_prob)

        if deviation >= EDGE_THRESHOLD_LOW:
            flags.append({
                "poly_market_id": pm.get("market_id"),
                "poly_question": pm.get("question"),
                "kalshi_ticker": km.get("ticker"),
                "kalshi_title": km.get("title"),
                "similarity": match["similarity"],
                "poly_prob": round(poly_prob, 4),
                "kalshi_prob": round(kalshi_prob, 4),
                "deviation": round(deviation, 4),
            })

    return flags
