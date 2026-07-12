"""Single-shot entrypoint for Railway Cron Jobs.

Runs one full bot cycle (analyze, then act on whatever's actionable) and
prunes/checkpoints the database, then exits. This is what makes the
deployment cheap: a cron job is only billed for the seconds each run
actually takes, not for the idle minutes between runs the way an
always-on deployment (psylocke/bot.py run as a worker) is.

Trade-off: Railway's cron minimum interval is 5 minutes, so this reacts to
a tracked wallet's trade within roughly one cron cycle rather than within
POLL_INTERVAL_SECONDS. If you want faster reaction later, switch the
service to run `python -m psylocke.bot` as an always-on worker instead --
everything else (schema, analysis, execution) is unchanged either way.
"""
from psylocke import analysis, bot, db
from psylocke.clob_execution import ExecutionClient
from psylocke.config import load_config
from psylocke.logging_setup import setup_logging
from psylocke.polymarket_data import DataAPIClient

logger = setup_logging("psylocke.run_cron")


def main() -> None:
    config = load_config()
    conn = db.get_connection(config.db_path)
    try:
        data_client = DataAPIClient(config.data_api_base, config.gamma_api_base)
        execution_client = ExecutionClient(config)

        for wallet in config.tracked_wallets:
            analysis.seed_wallet(conn, data_client, wallet)

        bot.run_once(conn, data_client, execution_client, config)

        pruned = db.prune_old_records(conn, config.retention_days)
        db.incremental_vacuum(conn)
        db.checkpoint(conn)
        logger.info("cron pass complete, pruned %d old record(s)", pruned)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
