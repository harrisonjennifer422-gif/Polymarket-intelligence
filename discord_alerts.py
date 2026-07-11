"""
Discord alerting via incoming webhook (no bot token, no gateway connection
needed - a webhook URL is enough to post messages into a channel).

Setup on your end:
  1. Discord server -> channel settings -> Integrations -> Webhooks -> New Webhook
  2. Copy the webhook URL
  3. Set it as the DISCORD_WEBHOOK_URL environment variable (Railway: under
     your service's Variables tab)

If DISCORD_WEBHOOK_URL is unset, alerting silently no-ops so the scanner
still runs fine locally without Discord configured.
"""

import requests

from config import DISCORD_WEBHOOK_URL, DISCORD_ALERT_MIN_DEVIATION, DISCORD_MAX_ALERTS_PER_RUN

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

    # Highest-conviction flags first, capped so a noisy run doesn't spam the channel
    alertable.sort(key=lambda x: -x[1]["deviation"])
    alertable = alertable[:DISCORD_MAX_ALERTS_PER_RUN]

    print(f"Discord: sending {len(alertable)} alert(s)...")
    sent = 0
    for kind, flag in alertable:
        embed = _build_embed(kind, flag, run_id)
        ok = _post_webhook({"embeds": [embed]})
        if ok:
            sent += 1
    print(f"Discord: {sent}/{len(alertable)} alert(s) posted successfully.")


def send_wallet_alerts(new_wallet_candidates: list, run_id: int):
    """
    Alerts only on NEWLY discovered wallet candidates (dedup handled by the
    caller via storage.upsert_wallet_candidate) - so you get pinged once per
    wallet, not every scan cycle.
    """
    if not DISCORD_WEBHOOK_URL:
        print("Discord: DISCORD_WEBHOOK_URL not set - skipping wallet alerts.")
        return

    if not new_wallet_candidates:
        print("Discord: no new wallet candidates this run - nothing sent.")
        return

    capped = new_wallet_candidates[:DISCORD_MAX_ALERTS_PER_RUN]
    print(f"Discord: sending {len(capped)} new wallet candidate alert(s)...")
    sent = 0
    for candidate in capped:
        embed = _build_wallet_embed(candidate, run_id)
        ok = _post_webhook({"embeds": [embed]})
        if ok:
            sent += 1
    print(f"Discord: {sent}/{len(capped)} wallet alert(s) posted successfully.")


def _build_embed(kind: str, flag: dict, run_id: int) -> dict:
    deviation_pp = flag["deviation"] * 100

    if kind == "arbitrage":
        title = f"🔶 Arbitrage flag — {deviation_pp:.1f}pp"
        color = _ARB_COLOR
        name_field = flag["event_title"]
    else:
        title = f"🔷 Cross-platform flag — {deviation_pp:.1f}pp"
        color = _CROSS_COLOR
        name_field = flag["poly_question"]

    fields = [
        {"name": "Market", "value": name_field[:1000] or "n/a", "inline": False},
        {"name": "Summary", "value": flag["summary"][:1000], "inline": False},
        {
            "name": "Verify before acting",
            "value": "\n".join(f"• {c}" for c in flag["checklist"])[:1000],
            "inline": False,
        },
        {"name": "CTA", "value": flag["cta"][:500], "inline": False},
    ]

    return {
        "title": title,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Polymarket Mispricing Scanner · run #{run_id}"},
    }


def _build_wallet_embed(candidate: dict, run_id: int) -> dict:
    age_months = candidate["wallet_age_days"] / 30.44
    name = candidate["username"] or f"{candidate['proxy_wallet'][:10]}…"

    fields = [
        {"name": "Wallet", "value": f"{name} (rank #{candidate['rank']})", "inline": True},
        {"name": "All-time PnL", "value": f"${candidate['pnl']:,.0f}", "inline": True},
        {"name": "Trades", "value": str(candidate["trade_count"]), "inline": True},
        {"name": "Age", "value": f"{age_months:.1f} months", "inline": True},
        {"name": "PnL/trade", "value": f"${candidate['pnl_per_trade']:,.0f}", "inline": True},
        {"name": "Summary", "value": candidate["summary"][:1000], "inline": False},
        {
            "name": "Verify before treating as a copy-trade candidate",
            "value": "\n".join(f"• {c}" for c in candidate["checklist"])[:1000],
            "inline": False,
        },
        {"name": "CTA", "value": candidate["cta"][:500], "inline": False},
    ]

    return {
        "title": f"🟢 New wallet candidate — {name}",
        "color": _WALLET_COLOR,
        "fields": fields,
        "footer": {"text": f"Polymarket Wallet Scanner · run #{run_id}"},
    }


def _post_webhook(payload: dict) -> bool:
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code >= 300:
            print(f"WARNING: Discord webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"WARNING: Discord webhook failed: {e}")
        return False
