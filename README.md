# Psylocke

Two cooperating bots that copy-trade specific Polymarket wallets on your
own account.

- **Psylocke 1** (`psylocke/signal_bot.py`) — watches the tracked wallets'
  public trade activity and writes a `signals` row for every new entry or
  exit it detects. It never touches trading credentials.
- **Psylocke 2** (`psylocke/execution_bot.py`) — the only process holding
  credentials. It polls the `signals` table, sizes and (optionally) places
  the corresponding order, and records what it did.

The two processes only communicate through the shared SQLite database at
`DB_PATH` (default `data/psylocke.db`) — there is no direct bot-to-bot
channel. That keeps signal generation and order execution auditable and
independently restartable.

## How a trade gets copied

1. Psylocke 1 polls each tracked wallet's activity via Polymarket's public
   Data API (`data-api.polymarket.com`).
2. On the first sight of a wallet it seeds a local ledger of that wallet's
   current holdings and marks existing history as seen, so only trades made
   *after* startup generate signals.
3. A new **BUY** becomes an `ENTRY` signal. Size is computed proportionally:
   `your_bankroll / tracked_wallet_portfolio_value * tracked_trade_usd`
   (see `BANKROLL_USD` below), optionally capped by `MAX_POSITION_USD`.
4. A new **SELL** becomes an `EXIT` signal carrying the fraction of the
   tracked wallet's position it closed (e.g. sold half → `exit_fraction=0.5`).
   Psylocke 2 exits the same fraction of *your* position in that market —
   not a dollar-matched exit — so you stay aligned with the trader you're
   following regardless of how your position size diverged from theirs.
5. Psylocke 2 only acts on signals older than `EXECUTION_DELAY_SECONDS`.
   This delay is deliberate: instant, exact copying is what makes a
   follow-bot conspicuous and easy to flag. Some lag is unavoidable anyway
   (you're reacting to on-chain data), so this just makes it explicit and
   configurable rather than accidental.
6. While `DRY_RUN=true` (the default), Psylocke 2 logs what it *would* do
   and updates its own simulated position ledger, but places no real order.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set BANKROLL_USD, leave DRY_RUN=true for now
```

Run each bot in its own terminal/process:

```bash
python -m psylocke.signal_bot
python -m psylocke.execution_bot
```

Inspect what happened:

```bash
sqlite3 data/psylocke.db "SELECT id, kind, wallet, title, tracked_side, computed_size_usd, exit_fraction, status FROM signals ORDER BY id DESC LIMIT 20;"
```

Once you've watched real signals come through in dry run and are comfortable
with the sizing, set `DRY_RUN=false` and fill in `POLY_PRIVATE_KEY` /
`POLY_FUNDER_ADDRESS` for the account that will actually trade.

## Deploying on Railway

Both bots share one SQLite file, and a Railway Volume only attaches to a
single service — so either way, this deploys as **one Railway service**,
not two. There are two ways to run that one service, and they trade cost
against reaction speed:

|                        | Cron Job (recommended default) | Always-on worker |
|------------------------|--------------------------------|-------------------|
| Entrypoint             | `run_cron.py`                  | `run_both.py` (via `Procfile`) |
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
5. Each run seeds any new wallets, does one signal-scan pass, one
   execution pass, then **prunes signals/dedup records older than
   `RETENTION_DAYS`** (default 30) and checkpoints the database — so the
   volume stays at a few MB indefinitely instead of growing every run.
   `PENDING` signals and the position ledgers are never pruned.

### Always-on worker (lower latency, higher cost)

Same steps, but leave the Custom Start Command unset (it'll use the
`Procfile`'s `worker: python run_both.py`) and don't set a Cron Schedule.
`run_both.py` launches `psylocke.signal_bot` and `psylocke.execution_bot`
as two long-lived subprocesses and tears both down together if either
exits, so Railway's restart policy restarts them in lockstep.

Either way, inspect what happened with `railway run` / `railway shell` to
get a shell with the volume mounted, then run the `sqlite3` query from the
Setup section above against `/data/psylocke.db`.

If you ever want Psylocke 1 and Psylocke 2 as fully independent Railway
services (separate logs/restarts/scaling), that needs a networked database
(e.g. Railway's managed Postgres addon) in place of the shared SQLite file
— more moving parts than either option above, so only worth it if you
specifically need that separation.

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
- **Rate limits.** `POLL_INTERVAL_SECONDS` controls how often each bot hits
  the API. Keep it reasonable (the default 30s) — hammering the API is both
  a ToS risk and unnecessary, since you're bounded by on-chain confirmation
  latency anyway.
- **This is not risk-free.** Copying another wallet's trades does not copy
  their conviction, timing, or risk management. `MAX_POSITION_USD` and
  `MIN_TRADE_USD` are safety rails, not guarantees — size them deliberately.
- **Credentials.** `POLY_PRIVATE_KEY` controls real funds. Keep `.env` out
  of version control (already in `.gitignore`) and never share it.

## Tests

```bash
pytest
```

Tests exercise the sizing math, exit-fraction mirroring, activity dedup, and
dry-run/live execution paths against fakes — they don't hit the network.
