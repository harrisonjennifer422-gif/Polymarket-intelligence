"""
Research layer: turns a raw flag (a number) into a research note (context +
a concrete next action). This is NOT a "buy/sell" signal generator - it's
the layer that forces you to do the verification step before acting,
which was the whole point of your original brief ("only act when your
model plus behavior data say the crowd is off").

Every note has three parts:
  1. What the flag means in plain language
  2. What to verify before trusting it (the actual due-diligence checklist)
  3. A CTA - a concrete next research/verification action, never "buy X"
"""


def annotate_arbitrage_flag(flag: dict) -> dict:
    deviation_pp = flag["deviation"] * 100
    direction = "overpriced (sum > 1.00)" if flag["outcome_sum"] > 1.0 else "underpriced (sum < 1.00)"

    summary = (
        f"The outcomes in \"{flag['event_title']}\" sum to {flag['outcome_sum']:.3f} "
        f"across {flag['num_outcomes']} markets — {deviation_pp:.1f}pp away from the "
        f"1.00 they should sum to if priced consistently. This group looks {direction}."
    )

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
        "Next step: pull the full order book for each leg in this group "
        "and simulate your actual fill price after slippage — do not size "
        "against the flagged mid-price alone."
    )

    return {**flag, "summary": summary, "checklist": checklist, "cta": cta}


def annotate_cross_platform_flag(flag: dict) -> dict:
    deviation_pp = flag["deviation"] * 100
    higher = "Polymarket" if flag["poly_prob"] > flag["kalshi_prob"] else "Kalshi"

    summary = (
        f"\"{flag['poly_question']}\" is priced at {flag['poly_prob']:.2f} on Polymarket "
        f"vs {flag['kalshi_prob']:.2f} on the matched Kalshi market "
        f"(\"{flag['kalshi_title']}\", similarity {flag['similarity']}) — "
        f"a {deviation_pp:.1f}pp gap, with {higher} pricing the higher probability."
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
        "Next step: re-read both markets' rules side by side and check "
        "today's volume on each before treating this gap as tradeable."
    )

    return {**flag, "summary": summary, "checklist": checklist, "cta": cta}
