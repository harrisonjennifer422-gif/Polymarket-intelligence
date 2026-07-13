"""
Orchestrates evidence gathering + independent trust-score cross-check.
This does NOT decide pass/fail on its own - that's confidence_gate.py's
job, combining this with resolution_rule_checker, market_relevance_checker,
and liquidity. This module's only responsibility: get the evidence and
make sure the trust scores are sane.
"""

from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="verification.source_verifier",
    requires_paid_api=False,
    estimated_cost_per_call_usd=0.0,
    free_fallback_strategy="N/A - pure local computation over already-fetched data, no external calls of any kind.",
))

from ingestion.external_sources import fetch_evidence
from features.source_quality_features import score_sources


def verify_sources(market_question: str, resolution_rule: str) -> dict:
    evidence = fetch_evidence(market_question, resolution_rule)

    # Cross-check the LLM's self-reported trust scores against our own
    # independent domain heuristic - don't blindly trust the model's
    # self-grading of its own sources.
    independent_scores = score_sources(evidence.get("source_urls", []))
    evidence["source_trust_scores"] = {
        url: round((evidence.get("source_trust_scores", {}).get(url, 0.5) + independent_scores.get(url, 0.5)) / 2, 3)
        for url in evidence.get("source_urls", [])
    }

    return evidence
