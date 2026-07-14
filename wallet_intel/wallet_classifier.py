"""
Classifies a wallet into the taxonomy using only already-computed features
(features/wallet_features.py + behavior_features.py) - pure rule-based
thresholding, thresholds live in config/wallet_scoring.yml.
"""

from config.loader import wallet_scoring as ws_cfg
from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="wallet_intel.wallet_classifier",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure rule-based thresholding over precomputed features.",
))

TAXONOMY_LABELS = [
    "smart_money", "specialist", "whale", "power_trader",
    "high_frequency_operator", "active_retail", "emotional_trader",
    "noise_wallet", "lucky_bettor",
]


def classify_wallet(features: dict, luck_flags: dict) -> str:
    """
    features: merged dict from wallet_features + behavior_features
    luck_flags: output of lucky_wallet_detector.detect_luck()
    """
    resolved = features.get("resolved_trade_count", 0)
    win_rate = features.get("win_rate")
    breadth = features.get("market_breadth", 0)
    trades_per_day = features.get("trades_per_day", 0.0)
    avg_notional = features.get("avg_notional_usd", 0.0)

    if resolved < ws_cfg.min_sample_size:
        return "noise_wallet"

    if luck_flags.get("is_luck_dominated"):
        return "lucky_bettor"

    if win_rate is not None and win_rate <= ws_cfg.taxonomy.emotional_max_win_rate and trades_per_day > 1.0:
        return "emotional_trader"

    if trades_per_day >= ws_cfg.taxonomy.high_frequency_min_trades_per_day:
        return "high_frequency_operator"

    if avg_notional >= ws_cfg.taxonomy.whale_min_avg_notional_usd:
        return "whale"

    if (win_rate is not None and win_rate >= ws_cfg.taxonomy.smart_money_min_win_rate
            and breadth >= ws_cfg.taxonomy.smart_money_min_breadth):
        return "smart_money"

    if breadth <= ws_cfg.taxonomy.specialist_max_breadth and win_rate is not None and win_rate >= 0.5:
        return "specialist"

    if trades_per_day >= 0.5 or breadth >= 2:
        return "power_trader"

    return "active_retail"
