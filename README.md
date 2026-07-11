[README.md](https://github.com/user-attachments/files/29910567/README.md)
# Polymarket Mispricing Scanner (v1)

A research tool that flags price deviations on Polymarket using **two real,
verifiable data sources** — no synthetic "AI model," no vibes.

## What it actually does

**Module 1 — Arbitrage scan (Polymarket-only)**
For multi-outcome "neg-risk" events (e.g. "Who will win the election?" split
into one Yes/No market per candidate), the Yes-prices across the group
should sum to ~1.00. If they sum to 1.11 or 0.89, that's a mechanical
mispricing — no external model required, just internal consistency.

**Module 2 — Cross-platform scan (Polymarket vs Kalshi)**
Matches Polymarket markets to Kalshi markets covering the same real-world
event (by title similarity), then flags cases where the two platforms'
implied probabilities diverge beyond your edge threshold. This is your
closest honest substitute for an "external model" — a second, independent
market pricing the same outcome.

Both modules write every flag to a local SQLite database
(`data/mispricing.db`) so you build a historical record over time, not just
a snapshot.

## Real data sources used

| Source | Endpoint | Auth | What we pull |
|---|---|---|---|
| Polymarket Gamma API | `gamma-api.polymarket.com` | None (public) | Events, markets, outcome prices, liquidity, volume |
| Kalshi public API | `api.elections.kalshi.com/trade-api/v2` | None (public, read-only) | Open markets, bid/ask, last price |

Both are the actual documented public endpoints as of mid-2026. No paid
data, no scraping, no guessed schemas.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

Optional flags:
```bash
python main.py --max-events 500 --max-kalshi 800
```

Run it on a cron/scheduled task (e.g. every 15–30 min) to build a real
time series of flags rather than one-off snapshots.

## Tuning

Edit `config.py`:
- `EDGE_THRESHOLD_LOW` / `EDGE_THRESHOLD_HIGH` — your 5–10pp deviation band
- `MIN_LIQUIDITY_USD` / `MIN_VOLUME_24H_USD` — filters out thin markets that
  show fake "mispricings" you can't actually trade
- `TITLE_MATCH_THRESHOLD` — how strict cross-platform matching is

## What this is NOT (important honesty check)

- **Not a trading bot.** It reads public data only. Placing orders requires
  wallet auth and is a deliberately separate, much higher-risk piece of
  code this doesn't touch.
- **Not guaranteed executable edge.** A flagged deviation ignores fees,
  spread, and slippage. Treat every flag as "worth investigating," not
  "worth executing."
- **Cross-platform matches are heuristic.** A 0.6-similarity match between
  a Polymarket and Kalshi market might resolve on subtly different
  criteria (different deadline, different source of truth, different
  rounding). Always read both markets' actual resolution rules before
  acting on a cross-platform flag.
- **No news-lag or wallet-scoring features yet.** Those were in your
  original brief but need either a paid low-latency news feed (news-lag)
  or a separate ingestion pipeline against Polymarket's Data API
  (wallet scoring/copy-trading). Both are buildable next — this v1
  deliberately scoped down to the mispricing scanner only, per your
  priority call.

## Known real constraints to respect

- Rate limits are Cloudflare-driven and throttle before rejecting — the
  `http_utils.py` backoff logic handles this, but don't crank
  `--max-events` past a few thousand without adding delay between pages.
- Gamma API prices can lag the live order book by a few seconds — fine for
  a scanner, not fine for latency-sensitive execution.
- Kalshi has published slightly different base URLs across its own docs
  over time. If `kalshi_client.py` starts getting 404s, check
  `docs.kalshi.com` for the current production host before assuming the
  code is broken.

## Discord alerts

1. In your Discord server: **Channel Settings → Integrations → Webhooks → New Webhook**. Copy the webhook URL.
2. Set it as an environment variable: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
3. Tune noise level with two more env vars (both optional):
   - `DISCORD_ALERT_MIN_DEVIATION` (default `0.07` = 7pp) — only flags at/above this get posted to Discord, even though *all* flags above your storage threshold still go into SQLite.
   - `DISCORD_MAX_ALERTS_PER_RUN` (default `8`) — caps alerts per scan so a messy run doesn't flood the channel.

If `DISCORD_WEBHOOK_URL` is unset, alerting silently no-ops — the scanner still runs and stores flags locally.

Every alert is a rich embed with: the flagged deviation, a plain-language summary, a "verify before acting" checklist, and a CTA — never a "buy/sell" instruction. The CTA always points to a research/verification action (check the order book, check resolution criteria, check volume), consistent with your original brief: research first, execute never — that part is a manual, human decision this tool won't make for you.

## Deploying on Railway

This repo is Railway-ready as-is (`Procfile` + `railway.json` included).

1. Push this folder to a GitHub repo (or use `railway up` from the CLI directly).
2. In Railway: **New Project → Deploy from GitHub repo**, select it.
3. Railway auto-detects Python via Nixpacks and installs `requirements.txt`.
4. Under your service's **Variables** tab, add:
   - `DISCORD_WEBHOOK_URL` — your webhook (required for alerts)
   - `DISCORD_ALERT_MIN_DEVIATION` — optional, default `0.07`
   - `DISCORD_MAX_ALERTS_PER_RUN` — optional, default `8`
   - `SCAN_INTERVAL_SECONDS` — optional, default `900` (15 min)
   - `MAX_EVENTS_PER_SCAN` / `MAX_KALSHI_PER_SCAN` — optional, defaults `300` / `500`
5. Deploy. The service runs `python main.py --loop` as a **worker** process (no web port needed) — it scans, sleeps for `SCAN_INTERVAL_SECONDS`, and repeats forever.

**Persistence note:** Railway's filesystem is ephemeral on redeploy — `data/mispricing.db` will reset when the service redeploys. For a v1 research tool this is an acceptable tradeoff (you still get live Discord alerts continuously), but if you want durable historical flag data across redeploys, attach a Railway Volume mounted at `/app/data` before you start relying on long-term trend queries against the DB.

## Next steps (in priority order, when you're ready)

1. **Wallet scoring / copy-trading** ✅ Built. See below.
2. **Dashboard**: point Grafana or a simple Streamlit app at
   `mispricing.db` — the schema is already flag-history-ready.
3. **News-lag**: only worth building once you've picked and paid for a
   real low-latency news feed — free feeds won't beat Polymarket's own
   price discovery.

## Wallet scoring (copy-trading research)

Uses Polymarket's public leaderboard (`/v1/leaderboard`) and per-wallet
trade history (`/activity`) — both real, documented, no-auth endpoints —
to surface wallets worth researching further, filtered on three things:

- **Genuinely profitable**: all-time PnL above `WALLET_MIN_PNL_USD` (default $2,000)
- **At least 3-6 months old**: `WALLET_MIN_AGE_DAYS` (default 90 = 3 months; raise to 180 for a 6-month floor). Age is derived from the wallet's actual earliest trade timestamp, not a guess.
- **"Little trade entries"**: `WALLET_MAX_TRADE_COUNT` (default 150) — wallets with more trades than this are treated as high-frequency/bot-like and skipped, since the goal is selective, high-conviction traders, not volume farmers.

Because each candidate wallet costs one extra API call, this scan runs less
often than the price scanners — every `WALLET_SCAN_EVERY_N_RUNS` cycles
(default every 4th scan). New qualifying wallets get a green Discord embed;
wallets you've already seen don't re-alert every cycle (tracked in the
`wallet_candidates` table), so your channel doesn't get spammed by the same
name repeatedly.

**Important, and consistent with your original brief:** this surfaces
*candidates for research*, not an auto-follow list. A wallet's historical
PnL on a public leaderboard is not proof of a repeatable edge — it could be
one lucky binary bet. Every wallet alert includes a verification checklist
(check current open positions, check PnL concentration, check realized vs.
unrealized PnL) and a CTA that always points to more research, never to
"follow this wallet."

Env vars to tune it:
```
WALLET_LEADERBOARD_POOL_SIZE=100   # top-N leaderboard wallets examined per scan
WALLET_MIN_AGE_DAYS=90             # 3 months; use 180 for 6 months
WALLET_MAX_TRADE_COUNT=150         # "little trade entries" ceiling
WALLET_MIN_PNL_USD=2000            # minimum all-time PnL to bother considering
WALLET_SCAN_EVERY_N_RUNS=4         # run wallet scan every Nth scan cycle
```

To force a wallet scan on a one-off local run: `python main.py --wallet-scan`
