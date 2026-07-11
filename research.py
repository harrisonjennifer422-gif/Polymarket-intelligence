"""
Research layer: turns a raw flag (a number) into a research note (context +
a directional read + a checklist + a CTA). This is NOT a "buy/sell" signal
generator in the sense of guaranteed advice - it explains the STANDARD
market mechanics of what a deviation like this classically implies, so you
can decide for yourself. Nothing here is financial advice, and every note
still ends with "verify before acting."

Each note has four parts:
  1. plain_summary - one or two short, non-technical sentences anyone can
     read at a glance, no jargon.
  2. summary - the fuller technical summary (kept for people who want it)
  3. checklist - what to verify before trusting it
  4. cta - a concrete next step, phrased as research/verification, and
     including a directional read (which side looks cheap) where the
     underlying mechanics support one.
"""


def annotate_arbitrage_flag(flag: dict) -> dict:
    deviation_pp = flag["deviation"] * 100
    underpriced = flag["outcome_sum"] < 1.0
    direction = "underpriced (sum < 1.00)" if underpriced else "overpriced (sum > 1.00)"

    summary = (
        f"The outcomes in \"{flag['event_title']}\" sum to {flag['outcome_sum']:.3f} "
        f"across {flag['num_outcomes']} markets — {deviation_pp:.1f}pp away from the "
        f"1.00 they should sum to if priced consistently. This group looks {direction}."
    )

    if underpriced:
        plain_summary = (
            f"\"{flag['event_title']}\" — all the possible outcomes together are "
            f"priced {deviation_pp:.1f} cents too CHEAP. In theory, buying \"Yes\" on "
            f"every single outcome in this group would cost you less than $1 total, "
            f"even though exactly one of them is guaranteed to pay out $1."
        )
        suggested_direction = "BUY the full Yes basket (all outcomes) — group as a whole is underpriced"
    else:
        plain_summary = (
            f"\"{flag['event_title']}\" — all the possible outcomes together are "
            f"priced {deviation_pp:.1f} cents too EXPENSIVE. This usually means the "
            f"group is hard to arbitrage fresh (it would require shorting/selling "
            f"across multiple legs at once), so treat this one as a research flag "
            f"rather than an easy trade."
        )
        suggested_direction = "No simple buy-side trade — group is overpriced, not underpriced"

    checklist = [
        "Confirm all outcome markets are genuinely mutually exclusive "
        "(same event, same resolution source, no overlap).",
        "Check the order book depth on the thinnest leg — the flagged "
        f"minimum liquidity was ${flag['min_liquidity']:.0f}; confirm you "
        "can actually size into it without moving the price past the edge.",
        "Check if the deviation is closing or widening — pull the last "
        "few scan runs for this event_id from mispricing.db before acting.",
    ]

    cta = (
        f"{suggested_direction}. Next step: pull the full order book for each "
        f"leg and simulate your actual fill price after slippage and fees — "
        f"do not size against the flagged mid-price alone. This is a research "
        f"signal, not financial advice."
    )

    return {
        **flag, "summary": summary, "plain_summary": plain_summary,
        "suggested_direction": suggested_direction, "checklist": checklist, "cta": cta,
    }


def annotate_cross_platform_flag(flag: dict) -> dict:
    deviation_pp = flag["deviation"] * 100
    poly_cheaper = flag["poly_prob"] < flag["kalshi_prob"]
    higher = "Kalshi" if poly_cheaper else "Polymarket"
    cheaper = "Polymarket" if poly_cheaper else "Kalshi"

    summary = (
        f"\"{flag['poly_question']}\" is priced at {flag['poly_prob']:.2f} on Polymarket "
        f"vs {flag['kalshi_prob']:.2f} on the matched Kalshi market "
        f"(\"{flag['kalshi_title']}\", similarity {flag['similarity']}) — "
        f"a {deviation_pp:.1f}pp gap, with {higher} pricing the higher probability."
    )

    plain_summary = (
        f"\"{flag['poly_question']}\" — {cheaper}'s \"Yes\" looks about "
        f"{deviation_pp:.1f} cents CHEAPER than the same bet on {higher}. "
        f"If these are really the same real-world event, that's a gap worth a "
        f"closer look."
    )

    suggested_direction = (
        f"{cheaper}'s Yes looks relatively cheap vs {higher}'s Yes on this "
        f"matched pair — worth checking, not a confirmed trade yet."
    )

    checklist = [
        "Read both markets' actual resolution criteria — a title-similarity "
        f"match of {flag['similarity']} is a candidate, not a confirmed pair. "
        "Different deadlines or source-of-truth rules invalidate the comparison.",
        "Check both platforms' current bid/ask spread, not just the flagged "
        "mid-price — a 10pp 'gap' can be mostly spread on a thin market.",
        "Check which side has more recent volume — the platform that just "
        "moved may be reacting to real news the other hasn't priced yet, "
        "which is a reason for the gap, not a mispricing.",
    ]

    cta = (
        f"{suggested_direction} Next step: re-read both markets' rules side "
        f"by side and check today's volume on each before treating this gap "
        f"as tradeable. This is a research signal, not financial advice."
    )

    return {
        **flag, "summary": summary, "plain_summary": plain_summary,
        "suggested_direction": suggested_direction, "checklist": checklist, "cta": cta,
    }


def annotate_wallet_candidate(candidate: dict) -> dict:
    """
    Expects `candidate` to already include the research dossier fields from
    wallet_research.build_dossier() (wins, losses, win_rate, behavioral_pattern,
    top_events, copytrade_fit, copytrade_reason, etc.) in addition to the
    base leaderboard fields (pnl, trade_count, wallet_age_days, etc.)
    """
    age_months = candidate["wallet_age_days"] / 30.44
    name = candidate["username"] or candidate["proxy_wallet"][:10] + "…"

    win_rate = candidate.get("win_rate")
    win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "not enough resolved trades yet"

    top_events_str = ", ".join(
        e["event"] for e in candidate.get("top_events", [])[:3]
    ) or "no distinct events found"

    verdict_label = "✅ Good copy-trade candidate" if candidate.get("copytrade_fit") else "⚠️ Not yet a fit"

    summary = (
        f"Wallet {name} (leaderboard rank {candidate['rank']}) shows "
        f"${candidate['pnl']:,.0f} all-time PnL across only "
        f"{candidate['trade_count']} trades (${candidate['pnl_per_trade']:,.0f}/trade) "
        f"over {age_months:.1f} months. Win rate: {win_rate_str} across "
        f"{candidate.get('resolved_count', 0)} resolved trades "
        f"({candidate.get('wins', 0)}W / {candidate.get('losses', 0)}L). "
        f"{candidate.get('behavioral_pattern', '')}"
    )

    plain_summary = (
        f"{name} has made about ${candidate['pnl']:,.0f} in profit over "
        f"{age_months:.1f} months, winning {win_rate_str} of resolved bets "
        f"({candidate.get('wins', 0)} wins, {candidate.get('losses', 0)} losses). "
        f"They mostly trade: {top_events_str}. "
        f"Verdict: {verdict_label} — {candidate.get('copytrade_reason', '')}"
    )

    checklist = [
        "Pull this wallet's live positions again yourself before acting — "
        f"as of this scan they had {candidate.get('open_positions_count', 0)} open "
        f"position(s) worth ~${candidate.get('open_exposure_usd', 0):,.0f}, but that "
        "changes over time.",
        "This win rate is based on RESOLVED trades only — unrealized PnL on "
        "open positions isn't locked in and could still go either way.",
        f"Only {candidate.get('resolved_count', 0)} resolved trades were used to "
        "compute this win rate — treat this as a small-sample signal, not "
        "a statistically proven edge.",
        "Cross-check whether their biggest wins came from fast, early entries "
        "(a real information/speed edge) versus late entries that happened "
        "to resolve well (luck).",
    ]

    cta = (
        f"{verdict_label}: {candidate.get('copytrade_reason', '')} Next step: "
        f"pull this wallet's current open positions and recent trade-level "
        f"detail yourself before treating them as a copy-trading candidate. "
        f"This is a research signal, not financial advice."
    )

    return {**candidate, "summary": summary, "plain_summary": plain_summary,
            "checklist": checklist, "cta": cta}
