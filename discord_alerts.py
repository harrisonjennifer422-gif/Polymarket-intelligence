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
