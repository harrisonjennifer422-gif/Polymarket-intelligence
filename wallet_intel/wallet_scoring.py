"""
CopyTradingSuitabilityScore (0-100) - weighted combination of components
from config/wallet_scoring.yml, multiplied by (1 - luck_penalty). Pure
arithmetic, no external calls.
"""

from config.loader import wallet_scoring as ws_cfg
from wallet_intel.lucky_wallet_detector import compute_penalty
from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="wallet_intel.wallet_scoring",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - weighted arithmetic scoring over precomputed features.",
))


def compute_copy_trade_score(features: dict, luck_flags: dict) -> int:
    win_rate = features.get("win_rate") or 0.0
    resolved_count = features.get("resolved_trade_count", 0)
    breadth = features.get("market_breadth", 0)
    max_drawdown = features.get("max_drawdown_usd", 0.0)

    sample_confidence = min(1.0, resolved_count / max(ws_cfg.min_sample_size * 3, 1))
    breadth_score = min(1.0, breadth / 5.0)
    drawdown_score = max(0.0, 1.0 - (max_drawdown / max(ws_cfg.max_acceptable_drawdown_usd, 1)))
    category_consistency = _category_consistency_score(features.get("category_performance", {}))

    w = ws_cfg.weights
    raw_score = (
        w.win_rate * win_rate
        + w.sample_confidence * sample_confidence
        + w.breadth * breadth_score
        + w.drawdown * drawdown_score
        + w.category_consistency * category_consistency
    )

    penalty = compute_penalty(luck_flags)
    final_score = raw_score * (1 - penalty)
    return round(max(0.0, min(1.0, final_score)) * 100)


def _category_consistency_score(category_performance: dict) -> float:
    """
    1.0 if the wallet performs well (positive PnL) across ALL categories it
    has resolved trades in; lower if performance swings between strongly
    positive and strongly negative across categories (inconsistent edge).
    """
    if not category_performance:
        return 0.5  # neutral - no category breakdown available
    win_rates = [v["win_rate"] for v in category_performance.values() if v.get("win_rate") is not None]
    if not win_rates:
        return 0.5
    avg = sum(win_rates) / len(win_rates)
    variance = sum((wr - avg) ** 2 for wr in win_rates) / len(win_rates)
    # Lower variance = more consistent = higher score
    return round(max(0.0, 1.0 - variance * 2), 4)


def recommendation_from_score(score: int) -> str:
    if score >= 65:
        return "copy"
    if score >= 40:
        return "watch"
    return "avoid"
