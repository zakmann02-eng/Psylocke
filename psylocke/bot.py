"""Psylocke: one bot, one process, one Polymarket account.

Each cycle it watches the tracked wallets (analysis.py), decides what to
copy, then acts on anything old enough to be actionable (execution.py).
There's no second bot and no inter-process handoff -- the `signals` table
is just this bot's own internal queue/audit trail, which is what makes the
deliberate EXECUTION_DELAY_SECONDS buffer and DRY_RUN review step possible
without adding real complexity.

For an always-on deployment, run this file directly (or `python -m
psylocke.bot`). For a scheduled/cron deployment (the cost-effective
default -- see README), use run_cron.py instead: it calls run_once() a
single time per invocation and then prunes/exits.
"""
from . import analysis, db, execution
from .clob_execution import ExecutionClient
from .config import load_config
from .logging_setup import setup_logging
from .polymarket_data import DataAPIClient, poll_forever

logger = setup_logging("psylocke.bot")


def run_once(conn, data_client: DataAPIClient, execution_client: ExecutionClient, config) -> None:
    """One full cycle: analyze, then act on whatever's actionable."""
    analysis.poll_once(conn, data_client, config)
    execution.poll_once(conn, execution_client, config)


def run():
    """Continuous loop for an always-on deployment."""
    config = load_config()
    conn = db.get_connection(config.db_path)
    data_client = DataAPIClient(config.data_api_base, config.gamma_api_base)
    execution_client = ExecutionClient(config)

    for wallet in config.tracked_wallets:
        analysis.seed_wallet(conn, data_client, wallet)

    logger.info(
        "Psylocke watching %d wallet(s), dry_run=%s, poll every %ss",
        len(config.tracked_wallets), config.dry_run, config.poll_interval_seconds,
    )
    poll_forever(
        lambda: run_once(conn, data_client, execution_client, config),
        config.poll_interval_seconds,
        logger,
    )


if __name__ == "__main__":
    run()
