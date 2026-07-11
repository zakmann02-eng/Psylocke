"""Psylocke 2 — execution bot.

Polls the `signals` table Psylocke 1 writes to and is the only process that
holds trading credentials or calls the CLOB API. Signals sit for
EXECUTION_DELAY_SECONDS before being actionable, and DRY_RUN defaults to
true, so nothing trades for real until both are deliberately turned off.
"""
from . import db
from .clob_execution import ExecutionClient
from .config import load_config
from .logging_setup import setup_logging
from .polymarket_data import poll_forever

logger = setup_logging("psylocke.execution_bot")


def _record_own_entry(conn, signal, size_shares: float, price: float) -> None:
    pos = db.get_own_position(conn, signal["token_id"])
    prior_shares = pos["shares"] if pos else 0.0
    prior_avg = pos["avg_price"] if pos else 0.0
    new_shares = prior_shares + size_shares
    new_avg = (
        ((prior_shares * prior_avg) + (size_shares * price)) / new_shares
        if new_shares > 0
        else 0.0
    )
    db.upsert_own_position(conn, signal["token_id"], signal["market_id"], new_shares, new_avg, signal["wallet"])


def _record_own_exit(conn, signal, size_shares: float) -> None:
    pos = db.get_own_position(conn, signal["token_id"])
    remaining = max(pos["shares"] - size_shares, 0.0)
    db.upsert_own_position(conn, signal["token_id"], pos["market_id"], remaining, pos["avg_price"], signal["wallet"])


def execute_entry(conn, execution_client, config, signal) -> None:
    size_usd = signal["computed_size_usd"]
    if not size_usd or size_usd <= 0:
        db.update_signal_status(conn, signal["id"], "SKIPPED", "no computed size (unknown wallet value or bankroll)")
        return
    if config.max_position_usd > 0:
        size_usd = min(size_usd, config.max_position_usd)
    if size_usd < config.min_trade_usd:
        db.update_signal_status(conn, signal["id"], "SKIPPED", f"below MIN_TRADE_USD (${size_usd:.2f})")
        return
    price = signal["tracked_price"]
    if price <= 0:
        db.update_signal_status(conn, signal["id"], "SKIPPED", "invalid price")
        return
    size_shares = size_usd / price

    if config.dry_run:
        logger.info(
            "[DRY RUN] would BUY %.4f shares of %s at %.4f (~$%.2f)",
            size_shares, signal["token_id"], price, size_usd,
        )
        _record_own_entry(conn, signal, size_shares, price)
        db.update_signal_status(conn, signal["id"], "EXECUTED_DRYRUN")
        return

    try:
        execution_client.place_order(signal["token_id"], "BUY", price, size_shares)
        _record_own_entry(conn, signal, size_shares, price)
        db.update_signal_status(conn, signal["id"], "EXECUTED")
    except Exception as exc:
        logger.exception("BUY order failed for signal %s", signal["id"])
        db.update_signal_status(conn, signal["id"], "FAILED", str(exc)[:500])


def execute_exit(conn, execution_client, config, signal) -> None:
    pos = db.get_own_position(conn, signal["token_id"])
    if not pos or pos["shares"] <= 0:
        db.update_signal_status(conn, signal["id"], "SKIPPED", "no open position to mirror exit from")
        return
    fraction = signal["exit_fraction"] or 1.0
    size_shares = pos["shares"] * fraction
    price = signal["tracked_price"]

    if config.dry_run:
        logger.info(
            "[DRY RUN] would SELL %.4f shares (%.0f%% of position) of %s at %.4f",
            size_shares, fraction * 100, signal["token_id"], price,
        )
        _record_own_exit(conn, signal, size_shares)
        db.update_signal_status(conn, signal["id"], "EXECUTED_DRYRUN")
        return

    try:
        execution_client.place_order(signal["token_id"], "SELL", price, size_shares)
        _record_own_exit(conn, signal, size_shares)
        db.update_signal_status(conn, signal["id"], "EXECUTED")
    except Exception as exc:
        logger.exception("SELL order failed for signal %s", signal["id"])
        db.update_signal_status(conn, signal["id"], "FAILED", str(exc)[:500])


def process_signal(conn, execution_client, config, signal) -> None:
    if signal["kind"] == "ENTRY":
        execute_entry(conn, execution_client, config, signal)
    else:
        execute_exit(conn, execution_client, config, signal)


def run():
    config = load_config()
    conn = db.get_connection(config.db_path)
    execution_client = ExecutionClient(config)

    def poll_once():
        for signal in db.get_actionable_signals(conn, config.execution_delay_seconds):
            process_signal(conn, execution_client, config, signal)

    logger.info(
        "Psylocke 2 (execution bot) started, dry_run=%s, execution_delay=%ss",
        config.dry_run, config.execution_delay_seconds,
    )
    poll_forever(poll_once, config.poll_interval_seconds, logger)


if __name__ == "__main__":
    run()
