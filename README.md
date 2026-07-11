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

## Full market coverage (not just Politics)

By default the scanner pulls events across **7 real Polymarket categories**
using their actual tag IDs, not just whatever's trending: politics, crypto,
geopolitics, finance, tech, and culture (sports is available but off by
default — enable via `CATEGORIES_TO_SCAN`). Without this, a generic
"all active events" pull tends to get dominated by whatever's most active
right now — often politics, since that's usually where the biggest
multi-outcome groups are. Explicit per-category pulls guarantee crypto,
Iran/Israel-type geopolitics markets, etc. are always represented.

```
CATEGORIES_TO_SCAN=politics,crypto,geopolitics,finance,tech,culture
MAX_EVENTS_PER_CATEGORY=60   # events pulled PER category, not total
```

Add `sports` to the list if you want that too. To force different
categories on a one-off local run: `python main.py --categories crypto,geopolitics`

## Plain-language alerts + directional guidance

Every alert now leads with a **plain-English summary** anyone can read at a
glance (no "deviation," "neg-risk," or "basis points"), followed by a
**directional read** — which side looks cheap, or whether buying the full
Yes basket is the textbook play — before the technical checklist for anyone
who wants to dig deeper.

**Important:** this is standard market mechanics explained clearly, not a
personalized recommendation. Arbitrage-underpriced groups have a
well-defined textbook response (buy the full Yes basket); overpriced groups
and cross-platform gaps get a directional read on which side looks cheap,
but always with a "verify first" checklist attached. Nothing here is
financial advice — you're still the one deciding whether and how to trade.

## Wallet research dossier (win/loss, behavior, copy-trade verdict)

Every newly-qualifying wallet gets a full research dossier, built from real
Polymarket data (not estimated or fabricated):

| What | Source | Real API field |
|---|---|---|
| Win/loss record | `/closed-positions` | `realizedPnl` per resolved position (positive = win, non-positive = loss) |
| Trades/day, behavioral pattern | `/activity` (full history) | trade timestamps, side (BUY/SELL), size, price |
| Events traded (top 5) | `/activity` | `eventSlug` / `title`, counted by frequency |
| Current open exposure | `/positions` | `currentValue` across open positions |
| Copy-trade fit verdict | Computed | rule-based: win rate ≥ 55% AND ≥ 3 resolved trades AND ≥ 2 distinct events |

**The copy-trade verdict is a transparent heuristic, not a guarantee.** It's
computed from real resolved trades, but a wallet can still have a good
verdict by luck, especially with a small sample. Every verdict comes with
its reasoning spelled out (e.g. *"71% win rate across 7 resolved trades
spanning 3 distinct events"* or *"only 2 resolved trades — too small a
sample to judge yet"*), so you can evaluate the reasoning, not just trust a
label.

This deeper research pass (2-3 extra API calls: activity, closed-positions,
open-positions) only runs on **newly discovered** qualifying wallets, not
on every wallet every cycle — keeping API usage bounded. Already-seen
wallets keep their last-built dossier in the database rather than
re-fetching it every scan.

Tune the verdict thresholds in `wallet_research.py`:
```python
MIN_RESOLVED_FOR_VERDICT = 3       # min resolved trades before trusting a win rate
WIN_RATE_FIT_THRESHOLD = 0.55      # min win rate for a positive verdict
MIN_DISTINCT_EVENTS_FOR_FIT = 2    # min distinct events (avoids one-lucky-bet false positives)
```

## Wallet alerts: same channel or a separate one?

Either works — it's your call:
- **Same channel** (default): just set `DISCORD_WEBHOOK_URL` and wallet
  alerts land there too, tagged with a green 🟢 embed so they're visually
  distinct from mispricing alerts.
- **Separate channel**: create a second webhook in a dedicated channel and
  set `DISCORD_WALLET_WEBHOOK_URL` to that URL. Wallet alerts go there
  instead, keeping your mispricing channel focused on price flags only.

If you're not seeing wallet alerts yet: remember the wallet scan only runs
every `WALLET_SCAN_EVERY_N_RUNS` cycles (default every 4th scan, ~1 hour at
the default 15-min interval) — check your Deploy Logs for a line like
`Wallet scan skipped this cycle (runs every 4 cycles...)` to confirm it's
working as intended, not broken. Lower `WALLET_SCAN_EVERY_N_RUNS` to `1` to
test it on every cycle while you're validating the setup.

## Discord alerts

1. In your Discord server: **Channel Settings → Integrations → Webhooks → New Webhook**. Copy the webhook URL.
2. Set it as an environment variable: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
3. Tune noise level with two more env vars (both optional):
   - `DISCORD_ALERT_MIN_DEVIATION` (default `0.07` = 7pp) — only flags at/above this get posted to Discord, even though *all* flags above your storage threshold still go into SQLite.
   - `DISCORD_MAX_ALERTS_PER_RUN` (default `8`) — caps alerts per scan so a messy run doesn't flood the channel.
4. Optional: `DISCORD_WALLET_WEBHOOK_URL` for a separate wallet-alerts channel (see above).

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
