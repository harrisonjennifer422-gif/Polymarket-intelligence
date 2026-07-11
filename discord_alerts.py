"""
Discord alerting via incoming webhooks (no bot token, no gateway connection
needed).

Setup:
  1. Discord server -> channel settings -> Integrations -> Webhooks -> New Webhook
  2. Copy the webhook URL
  3. Set DISCORD_WEBHOOK_URL as an env var (Railway: Variables tab)

Optional: set DISCORD_WALLET_WEBHOOK_URL to a DIFFERENT webhook if you want
wallet-candidate alerts in a separate channel from mispricing alerts. If
unset, wallet alerts fall back to the same channel as everything else.

If no webhook is configured at all, alerting silently no-ops so the scanner
still runs fine locally without Discord configured.

Embeds lead with a plain-language summary first (for non-technical
readers), then a directional read, then the technical detail and
verification checklist for anyone who wants to dig deeper.
"""

import requests

from config import (
    DISCORD_WEBHOOK_URL, DISCORD_WALLET_WEBHOOK_URL,
    DISCORD_ALERT_MIN_DEVIATION, DISCORD_MAX_ALERTS_PER_RUN,
)

_ARB_COLOR = 0xE67E22       # orange
_CROSS_COLOR = 0x3498DB     # blue
_WALLET_COLOR = 0x2ECC71    # green


def send_alerts(annotated_arb_flags: list, annotated_cross_flags: list, run_id: int):
    if not DISCORD_WEBHOOK_URL:
        print("Discord: DISCORD_WEBHOOK_URL not set - skipping alerts.")
        return

    alertable = [
        ("arbitrage", f) for f in annotated_arb_flags
        if f["deviation"] >= DISCORD_ALERT_MIN_DEVIATION
    ] + [
        ("cross_platform", f) for f in annotated_cross_flags
        if f["deviation"] >= DISCORD_ALERT_MIN_DEVIATION
    ]

    total_flags = len(annotated_arb_flags) + len(annotated_cross_flags)

    if not alertable:
        print(
            f"Discord: {total_flags} flag(s) found this run, but none reached "
            f"the {DISCORD_ALERT_MIN_DEVIATION*100:.1f}pp alert threshold "
            f"(DISCORD_ALERT_MIN_DEVIATION) - nothing sent."
        )
        return

    alertable.sort(key=lambda x: -x[1]["deviation"])
    alertable = alertable[:DISCORD_MAX_ALERTS_PER_RUN]

    print(f"Discord: sending {len(alertable)} alert(s)...")
    sent = 0
    for kind, flag in alertable:
        embed = _build_embed(kind, flag, run_id)
        ok = _post_webhook(DISCORD_WEBHOOK_URL, {"embeds": [embed]})
        if ok:
            sent += 1
    print(f"Discord: {sent}/{len(alertable)} alert(s) posted successfully.")


def send_wallet_alerts(new_wallet_candidates: list, run_id: int):
    """
    Alerts only on NEWLY discovered wallet candidates (dedup handled by the
    caller via storage.upsert_wallet_candidate). Uses DISCORD_WALLET_WEBHOOK_URL
    if set (a separate channel), otherwise falls back to the main webhook.
    """
    if not DISCORD_WALLET_WEBHOOK_URL:
        print("Discord: no wallet webhook configured - skipping wallet alerts.")
        return

    if not new_wallet_candidates:
        print("Discord: no new wallet candidates this run - nothing sent.")
        return

    capped = new_wallet_candidates[:DISCORD_MAX_ALERTS_PER_RUN]
    print(f"Discord: sending {len(capped)} new wallet candidate alert(s)...")
    sent = 0
    for candidate in capped:
        embed = _build_wallet_embed(candidate, run_id)
        ok = _post_webhook(DISCORD_WALLET_WEBHOOK_URL, {"embeds": [embed]})
        if ok:
            sent += 1
    print(f"Discord: {sent}/{len(capped)} wallet alert(s) posted successfully.")


def _build_embed(kind: str, flag: dict, run_id: int) -> dict:
    deviation_pp = flag["deviation"] * 100

    if kind == "arbitrage":
        title = f"🔶 {deviation_pp:.1f}pp mispricing — Polymarket internal check"
        color = _ARB_COLOR
        name_field = flag["event_title"]
    else:
        title = f"🔷 {deviation_pp:.1f}pp gap — Polymarket vs Kalshi"
        color = _CROSS_COLOR
        name_field = flag["poly_question"]

    # Plain-language summary leads - readable by anyone, no jargon.
    fields = [
        {"name": "Market", "value": name_field[:1000] or "n/a", "inline": False},
        {"name": "In plain terms", "value": flag["plain_summary"][:1000], "inline": False},
        {"name": "What to do next", "value": flag["cta"][:500], "inline": False},
        {
            "name": "Verify before acting (technical)",
            "value": "\n".join(f"• {c}" for c in flag["checklist"])[:1000],
            "inline": False,
        },
    ]

    return {
        "title": title,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Polymarket Alpha Engine · run #{run_id} · not financial advice"},
    }


def _build_wallet_embed(candidate: dict, run_id: int) -> dict:
    age_months = candidate["wallet_age_days"] / 30.44
    name = candidate["username"] or f"{candidate['proxy_wallet'][:10]}…"

    win_rate = candidate.get("win_rate")
    win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "N/A (too few resolved trades)"
    verdict_emoji = "✅" if candidate.get("copytrade_fit") else "⚠️"

    top_events_str = "\n".join(
        f"• {e['event']} ({e['trade_count']}x)" for e in candidate.get("top_events", [])[:5]
    ) or "No distinct events found"

    fields = [
        {"name": "Wallet", "value": f"{name} (rank #{candidate['rank']})", "inline": True},
        {"name": "All-time PnL", "value": f"${candidate['pnl']:,.0f}", "inline": True},
        {"name": "Age", "value": f"{age_months:.1f} months", "inline": True},
        {"name": "Trades", "value": str(candidate["trade_count"]), "inline": True},
        {"name": "Trades/day", "value": f"{candidate.get('trades_per_day', 0):.2f}", "inline": True},
        {"name": "PnL/trade", "value": f"${candidate['pnl_per_trade']:,.0f}", "inline": True},
        {
            "name": "Win/Loss record",
            "value": f"{win_rate_str} win rate — {candidate.get('wins', 0)}W / "
                     f"{candidate.get('losses', 0)}L ({candidate.get('resolved_count', 0)} resolved)",
            "inline": False,
        },
        {"name": "Events traded (top 5)", "value": top_events_str[:1000], "inline": False},
        {"name": "Behavioral pattern", "value": candidate.get("behavioral_pattern", "n/a")[:1000], "inline": False},
        {
            "name": "Open exposure right now",
            "value": f"{candidate.get('open_positions_count', 0)} open position(s), "
                     f"~${candidate.get('open_exposure_usd', 0):,.0f} total value",
            "inline": False,
        },
        {
            "name": f"{verdict_emoji} Copy-trade fit verdict",
            "value": candidate.get("copytrade_reason", "n/a")[:1000],
            "inline": False,
        },
        {"name": "What to do next", "value": candidate["cta"][:500], "inline": False},
    ]

    return {
        "title": f"🟢 New wallet candidate — {name}",
        "color": _WALLET_COLOR,
        "fields": fields,
        "footer": {"text": f"Polymarket Wallet Scanner · run #{run_id} · not financial advice"},
    }


def _post_webhook(webhook_url: str, payload: dict) -> bool:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code >= 300:
            print(f"WARNING: Discord webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"WARNING: Discord webhook failed: {e}")
        return False
