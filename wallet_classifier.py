"""
Combines classification + score + luck detection into the final
copy_trade_recommendation and why_copy_or_not explanation - the piece that
turns numbers into an actual sentence a human reads.
"""

from wallet_intel.wallet_classifier import classify_wallet
from wallet_intel.lucky_wallet_detector import detect_luck
from wallet_intel.wallet_scoring import compute_copy_trade_score, recommendation_from_score
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

    why = _build_explanation(features, luck_flags, behavior_label, score, recommendation)

    return {
        "behavior_label": behavior_label,
        "copy_trade_score": score,
        "copy_trade_recommendation": recommendation,
        "why_copy_or_not": why,
        "luck_flags": luck_flags,
    }


def _build_explanation(features, luck_flags, behavior_label, score, recommendation) -> str:
    win_rate = features.get("win_rate")
    resolved = features.get("resolved_trade_count", 0)
    breadth = features.get("market_breadth", 0)

    if luck_flags["is_luck_dominated"]:
        return (
            f"Classified as {behavior_label} (score {score}/100): "
            + "; ".join(luck_flags["reasons"])
            + ". Not enough evidence of a repeatable edge yet."
        )

    win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "unknown"
    return (
        f"Classified as {behavior_label} (score {score}/100): {win_rate_str} win rate "
        f"across {resolved} resolved trades spanning {breadth} distinct events, "
        f"with no luck-concentration or small-sample flags triggered."
    )
