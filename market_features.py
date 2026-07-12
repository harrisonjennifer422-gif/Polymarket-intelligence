"""
Behavioral features - the patterns wallet_intel/wallet_classifier.py uses
to tell "smart money" from "emotional trader" from "noise wallet". All
computed from real /activity timestamps and side (BUY/SELL), no fabricated
psychology.
"""

from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="features.behavior_features",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure local computation over already-fetched data, no external calls of any kind.",
))

import math
from collections import Counter
from datetime import datetime, timezone


def compute_behavior_features(activity: list) -> dict:
    if not activity:
        return {
            "directional_bias": 0.0, "timing_entropy": 0.0,
            "avg_holding_duration_days": None, "entry_timing_label": "unknown",
            "buy_ratio": None,
        }

    buy_count = sum(1 for a in activity if a["side"] == "BUY")
    sell_count = len(activity) - buy_count
    buy_ratio = round(buy_count / len(activity), 4)
    # directional_bias: -1 (always sells) to +1 (always buys)
    directional_bias = round((buy_count - sell_count) / len(activity), 4)

    timing_entropy = _compute_timing_entropy(activity)
    entry_timing_label = _classify_entry_timing(activity)

    return {
        "directional_bias": directional_bias,
        "buy_ratio": buy_ratio,
        "timing_entropy": timing_entropy,
        "avg_holding_duration_days": _estimate_avg_holding_duration(activity),
        "entry_timing_label": entry_timing_label,
    }


def _compute_timing_entropy(activity: list) -> float:
    """
    Shannon entropy of trade-hour-of-day distribution, normalized to 0-1.
    0 = every trade happens at the exact same hour (highly mechanical/bot-like
    or a person with one fixed routine); 1 = trades spread uniformly across
    all 24 hours (no discernible timing pattern).
    """
    hours = []
    for a in activity:
        ts = a.get("timestamp")
        if ts is None:
            continue
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        hours.append(hour)

    if not hours:
        return 0.0

    counts = Counter(hours)
    total = len(hours)
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())
    max_entropy = math.log2(24)
    return round(entropy / max_entropy, 4) if max_entropy else 0.0


def _classify_entry_timing(activity: list) -> str:
    """
    Placeholder heuristic pending real news-timestamp correlation (that
    requires the verification/historical_context LLM layer to know when
    news actually broke for each trade's market). Without that, we can
    only observe trade cadence, not true "early vs reactive vs late"
    relative to external events - so we report 'unknown' honestly rather
    than guessing.
    """
    return "unknown"


def _estimate_avg_holding_duration(activity: list) -> float:
    """
    Rough proxy: average gap between consecutive trades on the SAME event
    (a BUY followed later by a SELL on the same event_slug approximates a
    round-trip hold). This undercounts positions still open (no matching
    SELL yet) - those are excluded, which biases toward shorter *closed*
    holds only. Flagged as an estimate, not exact.
    """
    by_event = {}
    for a in sorted(activity, key=lambda x: x.get("timestamp") or 0):
        key = a.get("event_slug") or a.get("title")
        if not key:
            continue
        by_event.setdefault(key, []).append(a)

    durations_days = []
    for trades in by_event.values():
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]
        if buys and sells:
            first_buy_ts = buys[0]["timestamp"]
            first_sell_after = next((s["timestamp"] for s in sells if s["timestamp"] > first_buy_ts), None)
            if first_sell_after:
                durations_days.append((first_sell_after - first_buy_ts) / 86400)

    if not durations_days:
        return None
    return round(sum(durations_days) / len(durations_days), 2)
