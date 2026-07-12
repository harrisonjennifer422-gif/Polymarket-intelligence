"""
The enforcement gate. This is where "no source, no signal" actually gets
enforced - every requirement is checked explicitly, and the result is a
VerificationRecord (as a dict) with a transparent PASS/FAIL/INSUFFICIENT_EVIDENCE
status and a plain-English explanation of exactly why.

This function is deliberately strict: if verification is disabled (no
ANTHROPIC_API_KEY, or verification.yml enabled: false), it returns
INSUFFICIENT_EVIDENCE rather than PASS - an unverified signal is never
silently treated as verified.
"""

from datetime import datetime, timezone

from config.loader import verification as verification_cfg
from storage import db
from verification.source_verifier import verify_sources
from verification.resolution_rule_checker import check_resolution_match
from verification.event_matcher import check_news_currency
from verification.market_relevance_checker import check_market_relevance
from ingestion.external_sources import evidence_enabled
from config.cost_profile import CostProfile, register

MODULE_COST_PROFILE = register(CostProfile(
    module_name="verification.confidence_gate",
    requires_paid_api=True,  # conditionally - see notes
    estimated_cost_per_call_usd=0.07,  # inherits ingestion.external_sources' per-call cost
    free_fallback_strategy=(
        "Rule-based checks (liquidity via features.liquidity_features, cache "
        "lookup via storage.db) always run FIRST and for free. The paid "
        "evidence call only fires if: (1) verification.enabled=true, (2) "
        "liquidity already passed, AND (3) no cached verification exists for "
        "this market within verification_cache_hours. Any market failing the "
        "free liquidity check never reaches the paid step at all."
    ),
    notes="This module enforces free-checks-before-paid-checks ordering explicitly in code, "
          "not just in this description - see run_verification()'s early-return structure.",
))


def run_verification(market_id: str, market_url: str, market_question: str,
                      resolution_rule: str, market_features: dict) -> dict:
    """
    Returns a VerificationRecord-shaped dict. Checks the cache first (see
    config/verification.yml: verification_cache_hours) so a market isn't
    re-verified (re-charged) every single scan cycle.
    """
    if not verification_cfg.enabled:
        return _build_record(
            market_id, market_url, status="DISABLED",
            explanation="Verification is disabled in config/verification.yml. "
                        "This signal has NOT been checked against real news/evidence.",
        )

    cached = db.get_cached_verification(market_id, verification_cfg.verification_cache_hours)
    if cached:
        return cached

    if not evidence_enabled():
        record = _build_record(
            market_id, market_url, status="INSUFFICIENT_EVIDENCE",
            explanation="ANTHROPIC_API_KEY not set - cannot verify evidence.",
        )
        db.set_cached_verification(market_id, record)
        return record

    market_relevance = check_market_relevance(market_features)
    if not market_relevance["liquidity_sufficient"]:
        record = _build_record(
            market_id, market_url, status="FAIL",
            liquidity_sufficient=False,
            explanation="Market liquidity insufficient: " + "; ".join(market_relevance["report"]["failures"]),
        )
        db.set_cached_verification(market_id, record)
        return record

    evidence = verify_sources(market_question, resolution_rule)

    if not evidence.get("raw_ok"):
        record = _build_record(
            market_id, market_url, status="INSUFFICIENT_EVIDENCE",
            liquidity_sufficient=True,
            explanation=f"Evidence gathering failed: {evidence.get('summary', 'unknown error')}",
        )
        db.set_cached_verification(market_id, record)
        return record

    primary_count = evidence.get("primary_source_count", 0)
    secondary_count = evidence.get("secondary_source_count", 0)
    resolution_matches = check_resolution_match(evidence)
    news_current = check_news_currency(evidence)
    internally_consistent = bool(evidence.get("internally_consistent", False))

    failures = []
    if primary_count < verification_cfg.min_primary_sources:
        failures.append(f"only {primary_count} primary source(s), need {verification_cfg.min_primary_sources}")
    if secondary_count < verification_cfg.min_secondary_sources:
        failures.append(f"only {secondary_count} secondary source(s), need {verification_cfg.min_secondary_sources}")
    if not resolution_matches:
        failures.append("evidence found doesn't clearly match the market's resolution rule")
    if not news_current:
        failures.append("news is not current enough")
    if not internally_consistent:
        failures.append("sources found are internally inconsistent/contradictory")

    status = "FAIL" if failures else "PASS"
    confidence = _compute_confidence(evidence, failures)

    explanation = (
        evidence.get("summary", "")
        if status == "PASS"
        else f"{evidence.get('summary', '')} Verification failed: {'; '.join(failures)}."
    )

    record = _build_record(
        market_id, market_url, status=status,
        source_urls=evidence.get("source_urls", []),
        source_trust_scores=evidence.get("source_trust_scores", {}),
        primary_source_count=primary_count, secondary_source_count=secondary_count,
        event_matches_resolution_rule=resolution_matches, news_is_current=news_current,
        liquidity_sufficient=True, internally_consistent=internally_consistent,
        confidence=confidence, explanation=explanation,
    )
    db.set_cached_verification(market_id, record)
    return record


def _compute_confidence(evidence: dict, failures: list) -> float:
    if failures:
        return round(max(0.0, 0.5 - 0.1 * len(failures)), 2)
    trust_scores = list(evidence.get("source_trust_scores", {}).values())
    avg_trust = sum(trust_scores) / len(trust_scores) if trust_scores else 0.5
    return round(min(0.95, avg_trust), 2)


def _build_record(market_id, market_url, status, source_urls=None, source_trust_scores=None,
                   primary_source_count=0, secondary_source_count=0,
                   event_matches_resolution_rule=False, news_is_current=False,
                   liquidity_sufficient=False, internally_consistent=False,
                   confidence=0.0, explanation="") -> dict:
    return {
        "market_id": market_id, "market_url": market_url, "status": status,
        "source_urls": source_urls or [], "source_trust_scores": source_trust_scores or {},
        "primary_source_count": primary_source_count, "secondary_source_count": secondary_source_count,
        "event_matches_resolution_rule": event_matches_resolution_rule, "news_is_current": news_is_current,
        "liquidity_sufficient": liquidity_sufficient, "internally_consistent": internally_consistent,
        "confidence": confidence, "explanation": explanation,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
