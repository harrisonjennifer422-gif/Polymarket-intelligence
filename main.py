"""
Run a single scan cycle:
  1. Pull active Polymarket events (Gamma API)
  2. Pull open Kalshi markets (Kalshi public API)
  3. Run arbitrage_scan (Polymarket-only consistency check)
  4. Run cross_platform_scan (Polymarket vs Kalshi deviation)
  5. Store all flags in SQLite
  6. Print a human-readable summary

Usage:
    python main.py                 # scan up to default limits
    python main.py --max-events 200 --max-kalshi 300
"""

import argparse
import sys
import time

import storage
from polymarket_client import fetch_active_events
from kalshi_client import fetch_open_markets
from scanners import arbitrage_scan, cross_platform_scan
from research import annotate_arbitrage_flag, annotate_cross_platform_flag
from discord_alerts import send_alerts
from http_utils import ApiError
from config import SCAN_INTERVAL_SECONDS, MAX_EVENTS_PER_SCAN, MAX_KALSHI_PER_SCAN


def run_scan(max_events: int, max_kalshi: int):
    storage.init_db()
    run_id = storage.start_run()

    print(f"[run {run_id}] Fetching Polymarket events (limit={max_events})...")
    try:
        events = fetch_active_events(max_events=max_events)
    except ApiError as e:
        print(f"FAILED to fetch Polymarket events: {e}", file=sys.stderr)
        return
    print(f"[run {run_id}] Retrieved {len(events)} events.")

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

    # --- Research annotation: every flag gets context + a checklist + a CTA ---
    annotated_arb = [annotate_arbitrage_flag(f) for f in arb_flags]
    annotated_cross = [annotate_cross_platform_flag(f) for f in cross_flags]

    # --- Discord alerts (no-ops if DISCORD_WEBHOOK_URL isn't set) ---
    send_alerts(annotated_arb, annotated_cross, run_id)

    _print_summary(run_id, events, kalshi_markets, annotated_arb, annotated_cross)


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
            print(f"    CTA: {f['cta']}")

    if cross_flags:
        print("\n--- CROSS-PLATFORM FLAGS (Polymarket vs Kalshi) ---")
        for f in sorted(cross_flags, key=lambda x: -x["deviation"])[:15]:
            print(
                f"  [{f['deviation']*100:.1f}pp] PM: {f['poly_question']!r}"
                f"\n         KX: {f['kalshi_title']!r} (sim={f['similarity']})"
                f"\n         poly={f['poly_prob']:.3f} kalshi={f['kalshi_prob']:.3f}"
            )
            print(f"    CTA: {f['cta']}")

    if not arb_flags and not cross_flags:
        print("\nNo flags above threshold this run.")

    print("\nAll flags are stored in data/mispricing.db for historical tracking.")
    print("Reminder: verify liquidity, resolution criteria, and fees before")
    print("acting on any flag - these are research signals, not trade calls.")


def run_forever(max_events: int, max_kalshi: int, interval_seconds: int):
    """
    Continuous loop for Railway's worker process type - scans, sleeps,
    repeats. This is what keeps the service alive and scanning 24/7
    instead of running once and exiting.
    """
    print(f"Starting continuous scan loop (interval={interval_seconds}s). Ctrl+C to stop.")
    while True:
        try:
            run_scan(max_events=max_events, max_kalshi=max_kalshi)
        except Exception as e:
            # Never let one bad scan kill the whole worker - log and continue
            print(f"ERROR during scan (will retry next interval): {e}", file=sys.stderr)
        print(f"\nSleeping {interval_seconds}s until next scan...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Mispricing Scanner")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS_PER_SCAN,
                         help="Max Polymarket events to pull per run")
    parser.add_argument("--max-kalshi", type=int, default=MAX_KALSHI_PER_SCAN,
                         help="Max Kalshi markets to pull per run")
    parser.add_argument("--loop", action="store_true",
                         help="Run continuously (scan, sleep, repeat) - use this on Railway")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL_SECONDS,
                         help="Seconds between scans when using --loop")
    args = parser.parse_args()

    if args.loop:
        run_forever(args.max_events, args.max_kalshi, args.interval)
    else:
        run_scan(max_events=args.max_events, max_kalshi=args.max_kalshi)
