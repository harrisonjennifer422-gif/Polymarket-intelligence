"""
Turns a MarketIntelligenceReport into a DiscordAlertPayload-shaped dict.
All plain-language, non-technical explanations live here - technical
detail (raw edge numbers, similarity scores) is available in the report
but summarized in plain English for the alert itself.
"""

from datetime import datetime, timezone

from alerts.cta_builder import build_ctas
from config.loader import discord as discord_cfg
from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="alerts.alert_payload_builder",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure formatting over an already-built MarketIntelligenceReport.",
))

_DECISION_EMOJI = {"BUY_YES": "🟢", "BUY_NO": "🔴", "MONITOR": "🟡", "NO_TRADE": "⚪"}


def build_payload(report: dict, wallet_profiles: list) -> dict:
    mispricing = report.get("mispricing") or {}
    verification = report.get("verification") or {}
    historical = report.get("historical_context") or {}

    market_title = mispricing.get("_event_title") or mispricing.get("_question") or report["market_id"]
    emoji = _DECISION_EMOJI.get(report["decision_label"], "⚪")

    plain_explanation = _plain_explanation(mispricing, report)
    evidence_summary = _evidence_summary(verification)
    historical_summary = historical.get("precedent_summary", "No historical context available.")
    wallet_summary = _wallet_summary(wallet_profiles)
    main_risks, failure_conditions = _risks_and_failure(report, historical)

    decision_statement = _decision_statement(mispricing, report, market_title)

    ctas = build_ctas(
        market_url=report.get("market_url", ""),
        source_urls=verification.get("source_urls", []) or historical.get("source_urls", []),
        wallet_addresses=report.get("influential_wallets", []),
    )

    return {
        "title": f"{emoji} {market_title}",
        "market_url": report.get("market_url", ""),
        "decision_statement": decision_statement,
        "plain_explanation": plain_explanation,
        "evidence_summary": evidence_summary,
        "historical_summary": historical_summary,
        "wallet_summary": wallet_summary,
        "decision_label": report["decision_label"],
        "suggested_size_pct": report.get("suggested_size_pct_of_risk_budget", 0.0),
        "confidence": report.get("confidence_tier", "low"),
        "main_risks": main_risks,
        "failure_conditions": failure_conditions,
        "cta_buttons": ctas,
        "wallet_addresses": report.get("influential_wallets", []) if discord_cfg.show_wallet_addresses else [],
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def _decision_statement(mispricing: dict, report: dict, market_title: str) -> str:
    """
    Builds the explicit, contract-level decision line required for every
    alert - names the exact market, the exact side, and the exact price,
    rather than a bare "Buy YES"/"Buy NO".
    """
    label = report["decision_label"]
    signal_type = mispricing.get("signal_type", "")
    implied_prob = mispricing.get("implied_probability", 0.0)

    if label == "MONITOR":
        return f"Monitor only — \"{market_title}\": edge detected but not yet confirmed enough to act."
    if label == "NO_TRADE":
        return f"No trade — \"{market_title}\": {report.get('why_this_side', 'insufficient edge or evidence')}"

    side = "YES" if label == "BUY_YES" else "NO"

    if signal_type == "arbitrage":
        num_outcomes = mispricing.get("_num_outcomes", "several")
        outcome_sum = mispricing.get("implied_probability", 0.0)
        return (
            f"Buy YES on ALL {num_outcomes} outcomes in \"{market_title}\" "
            f"(combined cost {outcome_sum:.3f} per $1.00 guaranteed payout — "
            f"buy the full basket, not a single outcome)."
        )

    # Cross-platform / single binary market - name the exact contract
    return f"Buy {side} on \"{market_title}\" at {implied_prob:.2f} ({implied_prob*100:.0f}% implied probability)."


def _plain_explanation(mispricing: dict, report: dict) -> str:
    edge_pp = mispricing.get("edge_size", 0.0) * 100
    signal_type = mispricing.get("signal_type", "")
    direction = mispricing.get("direction", "HOLD")

    if signal_type == "arbitrage":
        outcome_sum = mispricing.get("implied_probability", 0.0)
        cheap_or_rich = "too CHEAP" if outcome_sum < 1.0 else "too EXPENSIVE"
        return (
            f"All the possible outcomes in this market together are priced "
            f"{edge_pp:.1f} cents {cheap_or_rich} relative to the $1.00 they "
            f"should add up to."
        )

    benchmark_source = mispricing.get("benchmark_source", "")
    source_label = "Kalshi (a real second market)" if benchmark_source == "kalshi" else "an AI-researched estimate"
    return (
        f"This market's price looks {edge_pp:.1f} cents off compared to {source_label}, "
        f"suggesting the {direction} side may be underpriced."
    )


def _evidence_summary(verification: dict) -> str:
    status = verification.get("status", "INSUFFICIENT_EVIDENCE")
    tier = verification.get("verification_tier")
    tier_label = {
        "free_rss": "✅ Verified via free RSS feeds (Reuters/AP/gov/AI-lab/tech news)",
        "paid_llm": "✅ Verified via AI-researched web search",
    }.get(tier, "")

    if status == "PASS":
        prefix = f"{tier_label}\n" if tier_label else ""
        return prefix + verification.get("explanation", "Evidence verified.")
    if status == "FAIL":
        return f"⚠️ Evidence check FAILED: {verification.get('explanation', '')}"
    return verification.get("explanation") or "Not enough evidence was found to verify this independently yet."


def _wallet_summary(wallet_profiles: list) -> str:
    if not wallet_profiles:
        return "No notable wallets currently tracked in this market."

    lines = []
    for w in wallet_profiles[:3]:
        name = w.get("username") or w.get("wallet_address", "")[:10] + "…"
        lines.append(
            f"{name}: {w.get('behavior_label', 'unknown')}, "
            f"copy-trade score {w.get('copy_trade_score', 0)}/100 "
            f"({w.get('copy_trade_recommendation', 'watch')})"
        )
    return "\n".join(lines)


def _risks_and_failure(report: dict, historical: dict) -> tuple:
    risks = ["Prediction markets can move suddenly on new information not yet reflected here."]
    if historical.get("resembles_failed_setup"):
        risks.append("This setup resembles past similar markets that did NOT resolve as hoped.")
    if report.get("verification", {}).get("status") != "PASS":
        risks.append("This has not been independently news-verified.")

    failure = report.get("invalidation_conditions", "Re-evaluate if the underlying edge closes.")
    return " ".join(risks), failure
