# Psylocke

A bot that copy-trades specific Polymarket wallets on your own account:
watches their public trade activity, decides what to copy, and enters/exits
the corresponding positions for you.

Internally it's split into two modules for clarity, not two processes:

- **`psylocke/analysis.py`** — watches the tracked wallets' public trade
  activity and writes a `signals` row for every new entry or exit it
  detects.
- **`psylocke/execution.py`** — reads that `signals` table, sizes and
  (optionally) places the corresponding order, and records what it did.
  The only place trading credentials get used.

They run in the same process, one cycle at a time (`psylocke/bot.py`). The
`signals` table isn't an inter-bot channel — it's this bot's own internal
queue and audit trail, which is what makes the deliberate delay-before-acting
and dry-run review step possible without real architectural complexity.

## How a trade gets copied

1. The bot polls each tracked wallet's activity via Polymarket's public
   Data API (`data-api.polymarket.com`).
2. On the first sight of a wallet it seeds a local ledger of that wallet's
   current holdings and marks existing history as seen, so only trades made
   *after* startup generate signals.
3. Every new trade's market is checked against `REQUIRE_US_MARKETS` (default
   `true`): if the market isn't tagged as a US-topic market (politics,
   elections, US economy — see `US_MARKET_TAGS`), no signal is created for
   it at all. The wallet's position ledger still updates either way, so
   later exit-fraction math stays correct even for markets you never copy.
   A market whose tags can't be determined is treated as non-US (fails
   closed) rather than risking a copy outside scope.
4. A new **BUY** becomes an `ENTRY` signal. Size is computed proportionally:
   `your_bankroll / tracked_wallet_portfolio_value * tracked_trade_usd`
   (see `BANKROLL_USD` below), optionally capped by `MAX_POSITION_USD`.
5. A new **SELL** becomes an `EXIT` signal carrying the fraction of the
   tracked wallet's position it closed (e.g. sold half → `exit_fraction=0.5`).
   The bot exits the same fraction of *your* position in that market — not
   a dollar-matched exit — so you stay aligned with the trader you're
   following regardless of how your position size diverged from theirs.
6. The bot only acts on signals older than `EXECUTION_DELAY_SECONDS`. This
   delay is deliberate: instant, exact copying is what makes a follow-bot
   conspicuous and easy to flag. Some lag is unavoidable anyway (you're
   reacting to on-chain data), so this just makes it explicit and
   configurable rather than accidental.
7. While `DRY_RUN=true` (the default), it logs what it *would* do and
   updates its own simulated position ledger, but places no real order.
8. Every signal created, and every action taken (entered, exited, or
   failed), is pushed to Telegram in real time if `TELEGRAM_BOT_TOKEN`/
   `TELEGRAM_CHAT_ID` are set — see below.

## Real-time visibility (Telegram)

The bot posts to Telegram as it works — a "new ENTRY/EXIT signal" message
when analysis detects something, followed by a "bought/sold X shares...
mirroring wallet Y" (or `SKIPPED`/`FAILED`) message when execution acts on
it. That gives you a live, human-readable feed of what it's doing.

1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts.
   Save the token it gives you (`TELEGRAM_BOT_TOKEN`).
2. Start a chat with your new bot (or add it to a group/channel), send it
   any message.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   find `"chat":{"id": ...}` in the response — that's `TELEGRAM_CHAT_ID`
   (negative for groups/channels).
4. Set both as environment variables (Railway Variables tab, not `.env` in
   the repo). Leave them blank to disable — the bot still logs locally
   either way, this is additive.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set BANKROLL_USD, leave DRY_RUN=true for now
```

Run it:

```bash
python -m psylocke.bot
```

Inspect what happened:

```bash
sqlite3 data/psylocke.db "SELECT id, kind, wallet, title, tracked_side, computed_size_usd, exit_fraction, status FROM signals ORDER BY id DESC LIMIT 20;"
```

Once you've watched real signals come through in dry run and are comfortable
with the sizing, set `DRY_RUN=false` and fill in `POLY_PRIVATE_KEY` /
`POLY_FUNDER_ADDRESS` for the account that will actually trade.

## Deploying on Railway

One Railway service, one process, either way — there are two ways to run
it, trading cost against reaction speed:

|                        | Cron Job (recommended default) | Always-on worker |
|------------------------|--------------------------------|-------------------|
| Entrypoint             | `run_cron.py`                  | `python -m psylocke.bot` (via `Procfile`) |
| Billed for             | Only the seconds each run takes | The full time the container is up, 24/7 |
| Reaction time to a new trade | Within one cron cycle (5 min minimum on Railway) | Within `POLL_INTERVAL_SECONDS` (default 30s) |
| Rough duty cycle       | A run that takes ~5-10s every 5 min is ~2-3% of a day | 100% of a day |

Copying a wallet's trade a few minutes late doesn't change *what* you copy,
just the fill price — for a bot whose main job is staying in sync with a
trader's positions rather than front-running them, that's usually a
reasonable trade for an order-of-magnitude cost cut. If you later decide
speed matters more than cost, switching is a one-line change (the start
command), not a rewrite.

### Cron Job setup (cost-effective default)

1. **New Railway project** → deploy from this GitHub repo.
2. In the service's Settings, set a **Custom Start Command** of
   `python run_cron.py` and a **Cron Schedule** of `*/5 * * * *` (every 5
   minutes — Railway's minimum interval). This overrides the `Procfile`.
3. **Attach a Volume** to the service, mounted at e.g. `/data`.
4. **Set environment variables** — everything in `.env.example`, plus
   `DB_PATH=/data/psylocke.db` so the database lands on the volume. Leave
   `DRY_RUN=true` until you've verified signals against real wallet
   activity; `POLY_PRIVATE_KEY`/`POLY_FUNDER_ADDRESS` only matter once you
   flip it to `false`.
5. Each run seeds any new wallets, does one analysis pass, one execution
   pass, then **prunes signals/dedup records older than `RETENTION_DAYS`**
   (default 30) and checkpoints the database — so the volume stays at a
   few MB indefinitely instead of growing every run. `PENDING` signals and
   the position ledgers are never pruned.

### Always-on worker (lower latency, higher cost)

Same steps, but leave the Custom Start Command unset (it'll use the
`Procfile`'s `worker: python -m psylocke.bot`) and don't set a Cron
Schedule. The bot seeds the tracked wallets once at startup, then loops
forever: analyze, execute, sleep `POLL_INTERVAL_SECONDS`, repeat.

Either way, inspect what happened with `railway run` / `railway shell` to
get a shell with the volume mounted, then run the `sqlite3` query from the
Setup section above against `/data/psylocke.db`.

## Before you go live — read this

- **This was built without network access to Polymarket's API.** The
  request/response field names in `psylocke/polymarket_data.py` (activity,
  positions, value) and the CLOB order flow in
  `psylocke/clob_execution.py` (`py-clob-client`) match Polymarket's
  documented API as of this writing, but neither has been exercised against
  a live response from inside this environment. Run in `DRY_RUN=true` first,
  compare a few rows in the `signals` table against what you can see on
  Polymarket/Polygonscan for the tracked wallets, and fix any field mismatch
  before trusting it with real funds.
- **Eligibility.** Confirm your account is eligible to trade on Polymarket
  under its terms and your jurisdiction before running this live.
- **Rate limits.** `POLL_INTERVAL_SECONDS` controls how often the bot hits
  the API in always-on mode. Keep it reasonable (the default 30s) —
  hammering the API is both a ToS risk and unnecessary, since you're
  bounded by on-chain confirmation latency anyway.
- **This is not risk-free.** Copying another wallet's trades does not copy
  their conviction, timing, or risk management. `MAX_POSITION_USD` and
  `MIN_TRADE_USD` are safety rails, not guarantees — size them deliberately.
- **Credentials.** `POLY_PRIVATE_KEY` controls real funds. Keep `.env` out
  of version control (already in `.gitignore`) and never share it.
- **`POLY_FUNDER_ADDRESS` / `POLY_SIGNATURE_TYPE` must match how the
  trading account logs into Polymarket**, or orders will try to settle
  against the wrong address. `POLY_FUNDER_ADDRESS` is not your raw wallet
  address — it's the proxy wallet Polymarket holds your USDC in, found at
  `polymarket.com/profile/<address>` after logging into that account. Set
  `POLY_SIGNATURE_TYPE` to `2` for a browser-wallet account (MetaMask,
  etc. — the default here), `1` for email/magic-link login, or `0` for a
  raw EOA with no proxy wallet at all.
- **`US_MARKET_TAGS`' default value is a best guess, not verified.**
  `psylocke/polymarket_data.py`'s `get_market_tags()` (Gamma API) hasn't
  been exercised against a live response any more than the Data API has.
  Before relying on `REQUIRE_US_MARKETS` to keep you scoped to US-topic
  markets, pull tags for a few real markets you expect to match (and a few
  you expect to be filtered out) and confirm `US_MARKET_TAGS` actually
  lines up with what Polymarket returns — the failure mode if it's wrong
  is fail-closed (signals get skipped), never fail-open.

## Tests

```bash
pytest
```

Tests exercise the sizing math, exit-fraction mirroring, activity dedup, and
dry-run/live execution paths against fakes — they don't hit the network.
