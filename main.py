"""
Run a single scan cycle:
  1. Pull active Polymarket events ACROSS MULTIPLE CATEGORIES (Gamma API) -
     politics, crypto, geopolitics, finance, tech, culture by default, not
     just whatever's trending.
  2. Pull open Kalshi markets (Kalshi public API)
  3. Run arbitrage_scan (Polymarket-only consistency check)
  4. Run cross_platform_scan (Polymarket vs Kalshi deviation)
  5. Store all flags in SQLite
  6. Print a human-readable summary
  7. Every ~Nth cycle: wallet-scoring scan

Usage:
    python main.py                          # scan up to default limits
    python main.py --categories crypto,geopolitics
    python main.py --wallet-scan             # force wallet scan on this run
"""

import argparse
import sys
import time

import storage
from polymarket_client import fetch_events_by_categories
from kalshi_client import fetch_open_markets
from scanners import arbitrage_scan, cross_platform_scan
from research import annotate_arbitrage_flag, annotate_cross_platform_flag, annotate_wallet_candidate
from discord_alerts import send_alerts, send_wallet_alerts
from wallet_scoring import find_wallet_candidates
from wallet_research import build_dossier
from http_utils import ApiError
from config import (
    SCAN_INTERVAL_SECONDS, MAX_KALSHI_PER_SCAN,
    WALLET_SCAN_EVERY_N_RUNS, CATEGORY_TAG_IDS, CATEGORIES_TO_SCAN,
    MAX_EVENTS_PER_CATEGORY,
)


def run_scan(categories: list, max_per_category: int, max_kalshi: int,
             include_wallet_scan: bool = False):
    storage.init_db()
    run_id = storage.start_run()

    print(f"[run {run_id}] Fetching Polymarket events across categories: "
          f"{', '.join(categories)} (up to {max_per_category} each)...")
    try:
        events = fetch_events_by_categories(CATEGORY_TAG_IDS, categories, max_per_category)
    except ApiError as e:
        print(f"FAILED to fetch Polymarket events: {e}", file=sys.stderr)
        return
    print(f"[run {run_id}] Retrieved {len(events)} events across "
          f"{len(categories)} categories.")

    print(f"[run {run_id}] Fetching Kalshi markets (limit={max_kalshi})...")
    try:
        kalshi_markets = fetch_open_markets(max_markets=max_kalshi)
    except ApiError as e:
        print(f"WARNING: Kalshi fetch failed, skipping cross-platform scan: {e}",
              file=sys.stderr)
        kalshi_markets = []
    print(f"[run {run_id}] Retrieved {len(kalshi_markets)} Kalshi markets.")

    poly_market_count = sum(len(e["markets"]) for e in events)

    # --- Module 1: arbitrage scan ---
    arb_flags = arbitrage_scan(events)
    for f in arb_flags:
        storage.insert_arbitrage_flag(
            run_id, f["event_id"], f["event_title"], f["outcome_sum"],
            f["deviation"], f["num_outcomes"], f["min_liquidity"],
        )

    # --- Module 2: cross-platform scan ---
    cross_flags = []
    if kalshi_markets:
        cross_flags = cross_platform_scan(events, kalshi_markets)
        for f in cross_flags:
            storage.insert_cross_platform_flag(
                run_id, f["poly_market_id"], f["poly_question"],
                f["kalshi_ticker"], f["kalshi_title"], f["similarity"],
                f["poly_prob"], f["kalshi_prob"], f["deviation"],
            )

    storage.finish_run(run_id, len(events), poly_market_count, len(kalshi_markets))

    # --- Research annotation: every flag gets a plain-language summary,
    # a directional suggestion, a checklist, and a CTA ---
    annotated_arb = [annotate_arbitrage_flag(f) for f in arb_flags]
    annotated_cross = [annotate_cross_platform_flag(f) for f in cross_flags]

    # --- Discord alerts (no-ops if DISCORD_WEBHOOK_URL isn't set) ---
    send_alerts(annotated_arb, annotated_cross, run_id)

    _print_summary(run_id, events, kalshi_markets, annotated_arb, annotated_cross)

    # --- Module 3: wallet scoring (profitable, aged, low-trade-count wallets) ---
    if include_wallet_scan:
        print(f"\n[run {run_id}] Running wallet candidate scan (leaderboard research)...")
        try:
            candidates = find_wallet_candidates()
        except ApiError as e:
            print(f"WARNING: wallet scan failed: {e}", file=sys.stderr)
            candidates = []

        new_candidates = []
        for c in candidates:
            is_new = storage.upsert_wallet_candidate(run_id, c)
            if is_new:
                new_candidates.append(c)

        # Deep research pass (win/loss, behavioral pattern, events traded,
        # copytrade verdict) - only run on NEWLY discovered wallets, since
        # each one costs 2-3 extra API calls. Already-seen wallets keep
        # their existing dossier in the DB rather than re-fetching every cycle.
        dossiers = []
        for c in new_candidates:
            try:
                dossier = build_dossier(c)
            except ApiError as e:
                print(f"WARNING: dossier build failed for {c['proxy_wallet']}: {e}",
                      file=sys.stderr)
                dossier = c  # fall back to base candidate fields only
            dossiers.append(dossier)
            storage.upsert_wallet_candidate(run_id, dossier)  # re-save with full dossier

        annotated_wallets = [annotate_wallet_candidate(d) for d in dossiers]
        send_wallet_alerts(annotated_wallets, run_id)

        print(f"[run {run_id}] Wallet scan: {len(candidates)} qualifying wallet(s) "
              f"found, {len(new_candidates)} newly discovered (full dossier built).")
        for c in sorted(candidates, key=lambda x: -x["pnl_per_trade"])[:10]:
            print(
                f"  {c['username'] or c['proxy_wallet'][:10]+'…'} | "
                f"pnl=${c['pnl']:,.0f} | trades={c['trade_count']} | "
                f"age={c['wallet_age_days']/30.44:.1f}mo | "
                f"$/trade={c['pnl_per_trade']:,.0f}"
            )
    else:
        print(f"\n[run {run_id}] Wallet scan skipped this cycle "
              f"(runs every {WALLET_SCAN_EVERY_N_RUNS} cycles - use --wallet-scan "
              f"to force it on a standalone run).")


def _print_summary(run_id, events, kalshi_markets, arb_flags, cross_flags):
    print("\n" + "=" * 60)
    print(f"SCAN RUN #{run_id} SUMMARY")
    print("=" * 60)
    print(f"Polymarket events scanned: {len(events)}")
    print(f"Kalshi markets scanned:    {len(kalshi_markets)}")
    print(f"Arbitrage flags:           {len(arb_flags)}")
    print(f"Cross-platform flags:      {len(cross_flags)}")

    if arb_flags:
        print("\n--- ARBITRAGE FLAGS (neg-risk outcome sum deviation) ---")
        for f in sorted(arb_flags, key=lambda x: -x["deviation"])[:15]:
            print(
                f"  [{f['deviation']*100:.1f}pp] {f['event_title']!r} "
                f"| sum={f['outcome_sum']:.3f} | outcomes={f['num_outcomes']} "
                f"| min_liquidity=${f['min_liquidity']:.0f}"
            )
            print(f"    Plain: {f['plain_summary']}")
            print(f"    CTA: {f['cta']}")

    if cross_flags:
        print("\n--- CROSS-PLATFORM FLAGS (Polymarket vs Kalshi) ---")
        for f in sorted(cross_flags, key=lambda x: -x["deviation"])[:15]:
            print(
                f"  [{f['deviation']*100:.1f}pp] PM: {f['poly_question']!r}"
                f"\n         KX: {f['kalshi_title']!r} (sim={f['similarity']})"
                f"\n         poly={f['poly_prob']:.3f} kalshi={f['kalshi_prob']:.3f}"
            )
            print(f"    Plain: {f['plain_summary']}")
            print(f"    CTA: {f['cta']}")

    if not arb_flags and not cross_flags:
        print("\nNo flags above threshold this run.")

    print("\nAll flags are stored in data/mispricing.db for historical tracking.")
    print("Reminder: verify liquidity, resolution criteria, and fees before")
    print("acting on any flag - these are research signals, not trade calls,")
    print("and nothing here is financial advice.")


def run_forever(categories: list, max_per_category: int, max_kalshi: int,
                interval_seconds: int):
    """
    Continuous loop for Railway's worker process type - scans, sleeps,
    repeats. Wallet scanning runs only every WALLET_SCAN_EVERY_N_RUNS cycles.
    """
    print(f"Starting continuous scan loop (interval={interval_seconds}s). Ctrl+C to stop.")
    cycle = 0
    while True:
        cycle += 1
        run_wallet_scan = (cycle % WALLET_SCAN_EVERY_N_RUNS == 0)
        try:
            run_scan(categories=categories, max_per_category=max_per_category,
                      max_kalshi=max_kalshi, include_wallet_scan=run_wallet_scan)
        except Exception as e:
            # Never let one bad scan kill the whole worker - log and continue
            print(f"ERROR during scan (will retry next interval): {e}", file=sys.stderr)
        print(f"\nSleeping {interval_seconds}s until next scan...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Alpha Research Engine")
    parser.add_argument("--categories", type=str,
                         default=",".join(CATEGORIES_TO_SCAN),
                         help="Comma-separated categories to scan, e.g. "
                              "'politics,crypto,geopolitics,finance,tech,culture,sports'")
    parser.add_argument("--max-events-per-category", type=int, default=MAX_EVENTS_PER_CATEGORY,
                         help="Max events to pull PER category per run")
    parser.add_argument("--max-kalshi", type=int, default=MAX_KALSHI_PER_SCAN,
                         help="Max Kalshi markets to pull per run")
    parser.add_argument("--loop", action="store_true",
                         help="Run continuously (scan, sleep, repeat) - use this on Railway")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL_SECONDS,
                         help="Seconds between scans when using --loop")
    parser.add_argument("--wallet-scan", action="store_true",
                         help="Include the wallet-scoring scan on this run (standalone mode only)")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    if args.loop:
        run_forever(categories, args.max_events_per_category, args.max_kalshi, args.interval)
    else:
        run_scan(categories=categories, max_per_category=args.max_events_per_category,
                  max_kalshi=args.max_kalshi, include_wallet_scan=args.wallet_scan)
