"""
Combines classification + score + luck detection into the final
copy_trade_recommendation and why_copy_or_not explanation - the piece that
turns numbers into an actual sentence a human reads.
"""

from wallet_intel.wallet_classifier import classify_wallet
from wallet_intel.lucky_wallet_detector import detect_luck
from wallet_intel.wallet_scoring import compute_copy_trade_score, recommendation_from_score
from config.loader import wallet_scoring as ws_cfg
from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="wallet_intel.copy_trade_filter",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - combines already-computed free scores into a final verdict.",
))


def evaluate_wallet(closed_positions: list, features: dict) -> dict:
    luck_flags = detect_luck(closed_positions, features)
    behavior_label = classify_wallet(features, luck_flags)
    score = compute_copy_trade_score(features, luck_flags)
    recommendation = recommendation_from_score(score)

    days_dormant = features.get("days_since_last_trade")
    recommendation, dormancy_note = _apply_dormancy_override(recommendation, days_dormant)

    why = _build_explanation(features, luck_flags, behavior_label, score, recommendation, dormancy_note)

    return {
        "behavior_label": behavior_label,
        "copy_trade_score": score,
        "copy_trade_recommendation": recommendation,
        "why_copy_or_not": why,
        "luck_flags": luck_flags,
        "days_since_last_trade": days_dormant,
    }


def _apply_dormancy_override(recommendation: str, days_dormant) -> tuple:
    """
    A wallet's historical score doesn't matter if it's gone quiet - this
    downgrades the recommendation regardless of how good the numbers look,
    since a copy-trade candidate needs to actually still be trading.
    """
    cfg = ws_cfg.activity_recency
    if days_dormant is None or days_dormant == float("inf"):
        return recommendation, None

    if days_dormant > cfg.max_days_dormant_for_watch:
        return "avoid", (
            f"⚠️ DORMANT: no trades in {days_dormant:.0f} days (over "
            f"{cfg.max_days_dormant_for_watch:.0f}-day cutoff) - likely inactive or abandoned wallet."
        )
    if days_dormant > cfg.max_days_dormant_for_copy:
        if recommendation == "copy":
            return "watch", (
                f"⚠️ Downgraded from 'copy' to 'watch': no trades in {days_dormant:.0f} days "
                f"(over the {cfg.max_days_dormant_for_copy:.0f}-day active-trading cutoff). "
                f"Historical record looks good, but this wallet isn't currently active."
            )
        return recommendation, (
            f"Note: no trades in {days_dormant:.0f} days - not currently active."
        )
    return recommendation, None


def _build_explanation(features, luck_flags, behavior_label, score, recommendation, dormancy_note) -> str:
    win_rate = features.get("win_rate")
    resolved = features.get("resolved_trade_count", 0)
    breadth = features.get("market_breadth", 0)

    if luck_flags["is_luck_dominated"]:
        base = (
            f"Classified as {behavior_label} (score {score}/100): "
            + "; ".join(luck_flags["reasons"])
            + ". Not enough evidence of a repeatable edge yet."
        )
    else:
        win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "unknown"
        base = (
            f"Classified as {behavior_label} (score {score}/100): {win_rate_str} win rate "
            f"across {resolved} resolved trades spanning {breadth} distinct events, "
            f"with no luck-concentration or small-sample flags triggered."
        )

    if dormancy_note:
        return f"{dormancy_note} {base}"
    return base
