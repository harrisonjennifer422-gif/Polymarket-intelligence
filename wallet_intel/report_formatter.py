"""
Renders a wallet evaluation into the EXACT output format specified by the
Polymarket Wallet Intelligence Layer v2.4 spec - used for the detailed
per-wallet text block (in addition to, not instead of, the structured
Discord embed fields already built by alerts/discord_formatter.py).
"""

from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="wallet_intel.report_formatter",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure text templating over already-computed wallet data.",
))

_ACTIVITY_PATTERN_DISPLAY = {
    "active_human_trader": "Active Human Trader",
    "consistent_semi_automated": "Consistent Semi-Automated",
    "high_frequency_bot": "High-Frequency Bot/Relayer",
    "inconsistent_activity": "Inconsistent Activity",
}


def render_wallet_report(wallet_record: dict) -> str:
    """
    wallet_record: the full dict assembled by main.py's _build_wallet_record
    (features + evaluation + entry data merged), already containing
    is_system_contract / activity_pattern_label / avg_active_days_per_week
    / is_bursty / copy_trade_score etc.
    """
    address = wallet_record.get("wallet_address", "unknown")
    is_sys_contract = wallet_record.get("is_system_contract", False)
    sys_contract_label = wallet_record.get("system_contract_label")

    address_type = (
        f"Smart Contract (Deposit Wallet) - {sys_contract_label}" if is_sys_contract
        else "Wallet (EOA-equivalent user proxy)"
    )

    score = wallet_record.get("copy_trade_score", 0)
    active_days = wallet_record.get("avg_active_days_per_week", 0.0)
    pattern_label = wallet_record.get("activity_pattern_label", "unknown")
    pattern_display = _ACTIVITY_PATTERN_DISPLAY.get(pattern_label, "Unknown")
    recommendation = _score_to_recommendation_label(score)
    risk_level = _score_to_risk_level(score)

    meets_requirement = active_days >= 2.0 and not wallet_record.get("is_bursty", False)
    requirement_note = (
        "meets the 2-3 active days/week requirement" if meets_requirement
        else "does NOT meet the 2-3 active days/week requirement"
    )

    positive_signals = _build_positive_signals(wallet_record)
    red_flags = _build_red_flags(wallet_record, is_sys_contract)
    summary = _build_summary(wallet_record, pattern_display, meets_requirement)

    return f"""**Wallet Address:** `{address}`
**Address Type:** {address_type}
**Score:** {score}/100
**Activity Pattern:** Active on {active_days:.1f} days per week (average)
**Classification:** {pattern_display}
**Recommendation:** {recommendation}

**Summary:**
{summary}

**Positive Signals:**
{positive_signals}

**Red Flags:**
{red_flags}

**Risk Level:** {risk_level}
**Copytrading Advice:**
{_build_copytrading_advice(wallet_record, recommendation, meets_requirement, requirement_note, is_sys_contract)}
"""


def _score_to_recommendation_label(score: int) -> str:
    if score >= 85:
        return "Strong Buy"
    if score >= 72:
        return "Buy"
    if score >= 60:
        return "Neutral"
    return "Avoid"


def _score_to_risk_level(score: int) -> str:
    if score >= 72:
        return "Low"
    if score >= 60:
        return "Medium"
    return "High"


def _build_positive_signals(w: dict) -> str:
    signals = []
    win_rate = w.get("win_rate")
    if win_rate is not None and win_rate >= 0.6:
        signals.append(f"- {win_rate*100:.0f}% win rate across {w.get('resolved_count', 0)} resolved trades")
    if not w.get("is_bursty", True) and w.get("avg_active_days_per_week", 0) >= 2:
        signals.append(f"- Consistent activity: {w.get('avg_active_days_per_week', 0):.1f} active days/week, not bursty")
    if w.get("days_since_last_trade", 999) <= 7:
        signals.append(f"- Recently active: last trade {w.get('days_since_last_trade')} day(s) ago")
    if w.get("distinct_events", 0) >= 3:
        signals.append(f"- Diversified across {w.get('distinct_events')} distinct events, not a one-hit wonder")
    if not signals:
        signals.append("- None strong enough to highlight")
    return "\n".join(signals)


def _build_red_flags(w: dict, is_sys_contract: bool) -> str:
    flags = []
    if is_sys_contract:
        flags.append(f"- ⚠️ This is a known Polymarket SYSTEM contract ({w.get('system_contract_label')}), not an individual trader - should never be copy-traded")
    if w.get("is_bursty"):
        flags.append("- Bursty activity pattern: concentrated trading then long silent gaps")
    if w.get("activity_pattern_label") == "high_frequency_bot":
        flags.append("- High-frequency, mechanically-timed pattern consistent with a bot/relayer, not a human trader")
    if w.get("resolved_count", 0) < 15:
        flags.append(f"- Small sample: only {w.get('resolved_count', 0)} resolved trades (15-20+ preferred for statistical relevance)")
    if w.get("luck_flags", {}).get("is_luck_dominated"):
        flags.append("- Flagged as luck-dominated: " + "; ".join(w.get("luck_flags", {}).get("reasons", [])))
    if w.get("days_since_last_trade", 0) and w.get("days_since_last_trade", 0) > 14:
        flags.append(f"- Inactive for {w.get('days_since_last_trade')} days - may no longer be actively trading")
    if not flags:
        flags.append("- None identified")
    return "\n".join(flags)


def _build_summary(w: dict, pattern_display: str, meets_requirement: bool) -> str:
    name = w.get("username") or w.get("wallet_address", "")[:10] + "…"
    win_rate = w.get("win_rate")
    win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "an unknown"
    req_phrase = "meets" if meets_requirement else "does not meet"
    return (
        f"{name} shows a {win_rate_str} win rate across {w.get('resolved_count', 0)} resolved trades, "
        f"classified as {pattern_display}. This wallet {req_phrase} the minimum activity-frequency bar "
        f"for a reliable copy-trading signal. {w.get('why_copy_or_not', '')}"
    )


def _build_copytrading_advice(w: dict, recommendation: str, meets_requirement: bool,
                               requirement_note: str, is_sys_contract: bool) -> str:
    if is_sys_contract:
        return "Do not copy-trade - this is shared platform infrastructure, not an individual trader."
    if recommendation == "Avoid":
        return f"Avoid copying this wallet. It {requirement_note}."
    size_pct = {"Strong Buy": "3-5%", "Buy": "1-3%", "Neutral": "0.5-1%"}.get(recommendation, "0%")
    return (
        f"If copying, size at roughly {size_pct} of risk budget per trade. "
        f"This wallet {requirement_note}. Address type: "
        f"{'Smart Contract' if is_sys_contract else 'Wallet (EOA-equivalent user proxy)'}."
    )
