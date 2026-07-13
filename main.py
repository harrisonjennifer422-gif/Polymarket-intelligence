"""
Polymarket Alpha Intelligence Engine v2 - orchestrator.

Runtime flow (matches the architecture doc):
  1. Ingest market data across categories + Kalshi + closed-events (free-tier
     historical data) - all free, always.
  2. Detect mispricing signals directly from raw market data (free: arbitrage
     + Kalshi cross-platform; conditionally paid: LLM-estimate fallback,
     disabled by default).
  3. For each candidate signal (there are usually few): fetch its order book,
     compute market features, run verification (free-tier-first) and
     historical precedent (free-tier-first), evaluate any relevant tracked
     wallets, and build a full MarketIntelligenceReport.
  4. Decide BUY_YES / BUY_NO / MONITOR / NO_TRADE via intelligence/decision_engine.
  5. Alert on Discord if thresholds are met.
  6. Periodically (every WALLET_SCAN_EVERY_N_RUNS cycles): scan the
     leaderboard for new qualifying wallets, build their full dossier via
     wallet_intel/*, alert on newly-discovered ones.

Usage:
    python main.py                    # one-off scan
    python main.py --loop             # continuous (Railway worker mode)
    python main.py --wallet-scan      # force the wallet scan on a one-off run
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import storage.db as db
from config.loader import (
    market_categories, risk as risk_cfg, discord as discord_cfg,
    wallet_scoring as ws_cfg, SCAN_INTERVAL_SECONDS, MAX_KALSHI_PER_SCAN,
    WALLET_SCAN_EVERY_N_RUNS,
)
from config.cost_profile import print_startup_report

from ingestion.polymarket_api import fetch_events_by_categories, fetch_closed_events
from ingestion.external_sources_kalshi import fetch_open_markets as fetch_kalshi_markets
from ingestion.orderbook_stream import fetch_book, compute_spread_and_depth
from ingestion.wallet_activity import (
    fetch_leaderboard, fetch_wallet_trade_summary, fetch_wallet_activity_detailed,
    fetch_wallet_closed_positions, fetch_wallet_open_positions,
)
from ingestion.http_utils import ApiError

from features.market_features import compute_market_features
from features.wallet_features import compute_wallet_features
from features.behavior_features import compute_behavior_features

from mispricing.edge_detector import detect_arbitrage, detect_cross_platform_edges
from mispricing.signal_ranker import rank_signals

from mispricing.probability_model import title_similarity
from intelligence.market_intelligence_builder import build_report
from wallet_intel.copy_trade_filter import evaluate_wallet

from alerts.alert_payload_builder import build_payload
from alerts.discord_formatter import send_market_alert, send_wallet_alert

MAX_SIGNALS_PROCESSED_PER_RUN = 20  # caps the expensive per-signal work (order book, verification)


def run_scan(categories: list, max_per_category: int, max_kalshi: int, include_wallet_scan: bool = False):
    db.init_db()
    run_id = db.start_run()

    print(f"[run {run_id}] Fetching Polymarket events across categories: "
          f"{', '.join(categories)} (up to {max_per_category} each)...")
    try:
        events = fetch_events_by_categories(categories, max_per_category)
    except ApiError as e:
        print(f"FAILED to fetch Polymarket events: {e}", file=sys.stderr)
        return
    print(f"[run {run_id}] Retrieved {len(events)} events.")

    print(f"[run {run_id}] Fetching Kalshi markets (limit={max_kalshi})...")
    try:
        kalshi_markets = fetch_kalshi_markets(max_markets=max_kalshi)
    except ApiError as e:
        print(f"WARNING: Kalshi fetch failed: {e}", file=sys.stderr)
        kalshi_markets = []
    print(f"[run {run_id}] Retrieved {len(kalshi_markets)} Kalshi markets.")

    print(f"[run {run_id}] Fetching closed events for historical precedent (free tier)...")
    try:
        closed_events = fetch_closed_events(max_events=max_per_category * len(categories))
    except ApiError as e:
        print(f"WARNING: closed-events fetch failed: {e}", file=sys.stderr)
        closed_events = []
    print(f"[run {run_id}] Retrieved {len(closed_events)} closed events.")

    poly_market_count = sum(len(e["markets"]) for e in events)

    # --- Free-tier mispricing detection (arbitrage + Kalshi cross-platform) ---
    arb_signals = detect_arbitrage(events)
    cross_signals = detect_cross_platform_edges(events, kalshi_markets)
    all_signals = rank_signals(arb_signals + cross_signals, top_n=MAX_SIGNALS_PROCESSED_PER_RUN)
    print(f"[run {run_id}] {len(arb_signals)} arbitrage signal(s), "
          f"{len(cross_signals)} cross-platform signal(s), processing top {len(all_signals)}.")

    market_lookup = {m["market_id"]: m for e in events for m in e["markets"]}
    event_lookup = {e["event_id"]: e for e in events}

    tracked_wallets = _load_tracked_wallets()

    reports_built = 0
    alerts_sent = 0
    for signal in all_signals:
        market = market_lookup.get(signal["market_id"])
        event = event_lookup.get(signal["market_id"])  # arbitrage signals key by event_id

        if market:
            question = market.get("question", "")
            resolution_rule = market.get("resolution_rule", "")
            market_title = question
            token_ids = market.get("clob_token_ids", [])
            book = fetch_book(token_ids[0]) if token_ids else None
            book_stats = compute_spread_and_depth(book) if book else {}
            m_features = compute_market_features(market, book_stats)
        elif event:
            question = event.get("title", "")
            resolution_rule = ""
            market_title = event.get("title", "")
            m_features = {
                "liquidity_usd": signal.get("_min_liquidity", 0.0),
                "volume_24h_usd": 0.0, "time_to_resolution_days": None,
            }
        else:
            continue  # signal references a market/event we no longer have - skip safely

        category = _infer_category(market_title, events)
        relevant_wallets = _find_relevant_wallets(market_title, tracked_wallets)

        report = build_report(
            mispricing_signal=signal, market_features=m_features,
            market_question=question, resolution_rule=resolution_rule,
            market_category=category, closed_events=closed_events,
            wallet_evaluations=relevant_wallets,
        )
        db.save_record(run_id, "MarketIntelligenceReport", report, market_id=signal["market_id"])
        reports_built += 1

        if _should_alert(report, signal):
            payload = build_payload(report, wallet_profiles=relevant_wallets)
            db.save_record(run_id, "DiscordAlertPayload", payload, market_id=signal["market_id"])
            if send_market_alert(payload):
                alerts_sent += 1

    db.finish_run(run_id, len(events), poly_market_count, len(kalshi_markets))
    _print_summary(run_id, events, kalshi_markets, all_signals, reports_built, alerts_sent)

    if include_wallet_scan:
        _run_wallet_scan(run_id)
    else:
        print(f"\n[run {run_id}] Wallet scan skipped this cycle "
              f"(runs every {WALLET_SCAN_EVERY_N_RUNS} cycles - use --wallet-scan to force it).")


def _should_alert(report: dict, signal: dict) -> bool:
    if report["decision_label"] not in ("BUY_YES", "BUY_NO"):
        return False
    return signal.get("edge_size", 0.0) >= discord_cfg.alert_min_deviation


def _infer_category(market_title: str, events: list) -> str:
    for e in events:
        if e.get("title") == market_title or any(m.get("question") == market_title for m in e["markets"]):
            return e.get("category", "unknown")
    return "unknown"


def _load_tracked_wallets() -> list:
    """Pulls previously-discovered wallet candidates from the DB for
    market-level relevance matching (does this market overlap a tracked
    wallet's known top events)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM wallet_candidates").fetchall()
    return [dict(r) for r in rows]


def _find_relevant_wallets(market_title: str, tracked_wallets: list, threshold: float = 0.5) -> list:
    """
    Approximates 'which tracked wallets are active in this market' using
    title-similarity against each wallet's stored top_events - an honest
    proxy given there's no free 'holders of this specific market' endpoint,
    rather than a fabricated direct lookup.
    """
    import json as _json
    relevant = []
    for w in tracked_wallets:
        top_events_raw = w.get("top_events")
        try:
            top_events = _json.loads(top_events_raw) if top_events_raw else []
        except (ValueError, TypeError):
            top_events = []
        for ev in top_events:
            if title_similarity(market_title, ev.get("event", "")) >= threshold:
                relevant.append({**w, "direction": None})  # direction unknown without a per-market holdings call
                break
    return relevant


def _run_wallet_scan(run_id: int):
    print(f"\n[run {run_id}] Running wallet leaderboard scan...")
    try:
        leaderboard = fetch_leaderboard(pool_size=ws_cfg.leaderboard_pool_size)
    except ApiError as e:
        print(f"WARNING: leaderboard fetch failed: {e}", file=sys.stderr)
        return

    now = datetime.now(timezone.utc).timestamp()
    new_count, evaluated_count = 0, 0

    for entry in leaderboard:
        if entry["pnl"] < ws_cfg.min_pnl_usd:
            continue
        wallet = entry["wallet_address"]
        if not wallet:
            continue

        try:
            summary = fetch_wallet_trade_summary(wallet, max_trades=ws_cfg.max_trade_count_for_selectivity)
        except ApiError:
            continue

        if summary["hit_cap"] or summary["trade_count"] == 0 or summary["first_trade_ts"] is None:
            continue

        wallet_age_days = (now - summary["first_trade_ts"]) / 86400
        if wallet_age_days < ws_cfg.min_wallet_age_days:
            continue

        try:
            activity = fetch_wallet_activity_detailed(wallet, limit=max(summary["trade_count"], 1))
            closed_positions = fetch_wallet_closed_positions(wallet)
            open_positions = fetch_wallet_open_positions(wallet)
        except ApiError as e:
            print(f"WARNING: dossier fetch failed for {wallet}: {e}", file=sys.stderr)
            continue

        features = compute_wallet_features(wallet, activity, closed_positions, open_positions, wallet_age_days)
        features.update(compute_behavior_features(activity))
        features["wallet_age_days"] = round(wallet_age_days, 1)

        evaluation = evaluate_wallet(closed_positions, features)
        evaluated_count += 1

        wallet_record = _build_wallet_record(
            wallet=wallet, entry=entry, features=features, evaluation=evaluation,
            activity=activity, closed_positions=closed_positions, open_positions=open_positions,
        )

        is_new = db.upsert_wallet_candidate(run_id, wallet_record)
        if is_new:
            new_count += 1
            if send_wallet_alert(wallet_record):
                print(f"  Alerted on new wallet candidate: {wallet_record.get('username') or wallet}")

    print(f"[run {run_id}] Wallet scan: {evaluated_count} wallet(s) evaluated, {new_count} newly discovered.")


def _build_wallet_record(wallet: str, entry: dict, features: dict, evaluation: dict,
                          activity: list, closed_positions: list, open_positions: list) -> dict:
    """
    Adapter between features/wallet_features.py's canonical field names
    (resolved_trade_count, market_breadth, avg_notional_usd - matching
    storage/schemas.py's WalletProfile) and storage/db.py's wallet_candidates
    table columns (resolved_count, distinct_events, avg_trade_size_usd, plus
    win/loss counts and a plain-language behavioral_pattern string that
    aren't computed elsewhere). Keeps features/*.py's output canonical while
    this is where the richer per-wallet dossier gets assembled for storage
    and Discord display.
    """
    wins = [p for p in closed_positions if p["realized_pnl"] > 0]
    losses = [p for p in closed_positions if p["realized_pnl"] <= 0]
    resolved_count = len(wins) + len(losses)
    total_realized_pnl = sum(p["realized_pnl"] for p in closed_positions) if closed_positions else 0.0

    trade_count = features.get("trade_count", 0)
    pnl_lifetime = features.get("pnl_lifetime", entry.get("pnl", 0.0))
    pnl_per_trade = round(pnl_lifetime / trade_count, 2) if trade_count else 0.0

    notionals = [a["notional_usd"] for a in activity if a.get("notional_usd")]
    largest_trade_usd = round(max(notionals), 2) if notionals else 0.0

    open_exposure_usd = round(sum(p.get("current_value", 0.0) for p in open_positions), 2)

    behavioral_pattern = _describe_behavior(
        trades_per_day=features.get("trades_per_day", 0.0),
        distinct_events=features.get("market_breadth", 0),
        buy_ratio=features.get("buy_ratio"),
        avg_trade_size_usd=features.get("avg_notional_usd", 0.0),
        largest_trade_usd=largest_trade_usd,
    )

    return {
        "wallet_address": wallet, "username": entry.get("username"), "rank": entry.get("rank"),
        "pnl": entry.get("pnl", 0.0), "vol": entry.get("vol", 0.0),
        "trade_count": trade_count, "wallet_age_days": features.get("wallet_age_days", 0.0),
        "pnl_per_trade": pnl_per_trade,
        "wins": len(wins), "losses": len(losses), "resolved_count": resolved_count,
        "win_rate": features.get("win_rate"), "avg_win": round(sum(p["realized_pnl"] for p in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(p["realized_pnl"] for p in losses) / len(losses), 2) if losses else 0.0,
        "total_realized_pnl": round(total_realized_pnl, 2),
        "trades_per_day": features.get("trades_per_day", 0.0),
        "distinct_events": features.get("market_breadth", 0),
        "top_events": features.get("top_events", []),
        "buy_ratio": features.get("buy_ratio"),
        "avg_trade_size_usd": features.get("avg_notional_usd", 0.0),
        "largest_trade_usd": largest_trade_usd,
        "behavioral_pattern": behavioral_pattern,
        "open_positions_count": len(open_positions),
        "open_exposure_usd": open_exposure_usd,
        "behavior_label": evaluation.get("behavior_label"),
        "copy_trade_score": evaluation.get("copy_trade_score"),
        "copy_trade_recommendation": evaluation.get("copy_trade_recommendation"),
        "why_copy_or_not": evaluation.get("why_copy_or_not"),
        "pnl_lifetime": pnl_lifetime,
    }


def _describe_behavior(trades_per_day, distinct_events, buy_ratio, avg_trade_size_usd, largest_trade_usd) -> str:
    concentration = "concentrated in a small handful of events" if distinct_events <= 3 else "diversified across many events"
    if buy_ratio is None:
        directionality = "unknown buy/sell mix"
    elif buy_ratio >= 0.8:
        directionality = "almost always opens new positions rather than exiting early"
    elif buy_ratio <= 0.2:
        directionality = "mostly exits/closes positions rather than opening fresh ones"
    else:
        directionality = "a balanced mix of opening and closing positions"

    return (
        f"Trades about {trades_per_day:.2f}x/day on average, {concentration} "
        f"({distinct_events} distinct events), with {directionality}. "
        f"Average trade size is roughly ${avg_trade_size_usd:,.0f}, with a "
        f"largest single trade of ${largest_trade_usd:,.0f}."
    )


def _print_summary(run_id, events, kalshi_markets, signals, reports_built, alerts_sent):
    print("\n" + "=" * 60)
    print(f"SCAN RUN #{run_id} SUMMARY")
    print("=" * 60)
    print(f"Polymarket events scanned: {len(events)}")
    print(f"Kalshi markets scanned:    {len(kalshi_markets)}")
    print(f"Signals processed:         {len(signals)}")
    print(f"Intelligence reports built:{reports_built}")
    print(f"Discord alerts sent:       {alerts_sent}")
    print("\nAll reports stored in the database for historical tracking.")
    print("Reminder: these are research signals, not trade calls, and")
    print("nothing here is financial advice.")


def run_forever(categories: list, max_per_category: int, max_kalshi: int, interval_seconds: int):
    print(f"Starting continuous scan loop (interval={interval_seconds}s). Ctrl+C to stop.")
    cycle = 0
    while True:
        cycle += 1
        run_wallet_scan = (cycle % WALLET_SCAN_EVERY_N_RUNS == 0)
        try:
            run_scan(categories=categories, max_per_category=max_per_category,
                      max_kalshi=max_kalshi, include_wallet_scan=run_wallet_scan)
        except Exception as e:
            print(f"ERROR during scan (will retry next interval): {e}", file=sys.stderr)
        print(f"\nSleeping {interval_seconds}s until next scan...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    print_startup_report()  # shows which modules are paid/free and current settings

    parser = argparse.ArgumentParser(description="Polymarket Alpha Intelligence Engine v2")
    parser.add_argument("--categories", type=str, default=",".join(market_categories.categories_to_scan))
    parser.add_argument("--max-events-per-category", type=int, default=market_categories.max_events_per_category)
    parser.add_argument("--max-kalshi", type=int, default=MAX_KALSHI_PER_SCAN)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL_SECONDS)
    parser.add_argument("--wallet-scan", action="store_true")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    if args.loop:
        run_forever(categories, args.max_events_per_category, args.max_kalshi, args.interval)
    else:
        run_scan(categories=categories, max_per_category=args.max_events_per_category,
                  max_kalshi=args.max_kalshi, include_wallet_scan=args.wallet_scan)
