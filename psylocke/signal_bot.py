"""Psylocke 1 — signal/analysis bot.

Watches the tracked wallets' public on-chain activity and turns each new
trade into a row in the shared `signals` table. It never places an order
itself: Psylocke 2 (execution_bot.py) is the only process with access to
trading credentials, so a bug or bad signal here can be reviewed (or
paused) before it touches real funds.
"""
from . import db
from .config import load_config
from .logging_setup import setup_logging
from .polymarket_data import DataAPIClient, poll_forever

logger = setup_logging("psylocke.signal_bot")


def _extract_trade_fields(item: dict) -> dict:
    price = float(item.get("price", 0.0))
    shares = float(item.get("size", 0.0))
    usd_size = float(item.get("usdcSize") or (shares * price))
    return {
        "tx_hash": item["transactionHash"],
        "token_id": item["asset"],
        "market_id": item.get("conditionId"),
        "side": item["side"],
        "price": price,
        "shares": shares,
        "usd_size": usd_size,
        "title": item.get("title"),
        "outcome": item.get("outcome"),
    }


def seed_wallet(conn, data_client: DataAPIClient, wallet: str) -> None:
    """On first sight of a wallet, snapshot its current holdings and mark all
    existing activity as seen so we only react to trades made from now on
    rather than replaying the wallet's entire history as fresh signals.
    """
    if db.is_wallet_seeded(conn, wallet):
        return
    for pos in data_client.get_positions(wallet):
        token_id = pos.get("asset")
        if token_id:
            db.set_wallet_shares(conn, wallet, token_id, float(pos.get("size", 0.0)))
    for item in data_client.get_activity(wallet, limit=200):
        tx_hash = item.get("transactionHash")
        if tx_hash:
            db.mark_seen(conn, wallet, tx_hash, seeded=True)
    db.mark_wallet_seeded(conn, wallet)
    logger.info("seeded wallet %s", wallet)


def process_activity_item(conn, wallet: str, item: dict, bankroll_usd: float, data_client: DataAPIClient = None):
    """Turn one raw activity item into a signal row, if it's new. Returns the
    new signal id, or None if this trade was already processed."""
    trade = _extract_trade_fields(item)
    if db.has_seen(conn, wallet, trade["tx_hash"]):
        return None
    db.mark_seen(conn, wallet, trade["tx_hash"])

    prior_shares = db.get_wallet_shares(conn, wallet, trade["token_id"])
    wallet_value = None
    computed_size_usd = None
    exit_fraction = None

    if trade["side"] == "BUY":
        kind = "ENTRY"
        db.set_wallet_shares(conn, wallet, trade["token_id"], prior_shares + trade["shares"])
        if data_client is not None:
            wallet_value = data_client.get_portfolio_value(wallet)
        if wallet_value and wallet_value > 0 and bankroll_usd > 0:
            computed_size_usd = (bankroll_usd / wallet_value) * trade["usd_size"]
    else:
        kind = "EXIT"
        db.set_wallet_shares(conn, wallet, trade["token_id"], max(prior_shares - trade["shares"], 0.0))
        exit_fraction = min(trade["shares"] / prior_shares, 1.0) if prior_shares > 0 else 1.0

    return db.insert_signal(
        conn,
        tx_hash=trade["tx_hash"],
        wallet=wallet,
        market_id=trade["market_id"],
        token_id=trade["token_id"],
        outcome=trade["outcome"],
        title=trade["title"],
        kind=kind,
        tracked_side=trade["side"],
        tracked_price=trade["price"],
        tracked_size_usd=trade["usd_size"],
        tracked_wallet_value_usd=wallet_value,
        exit_fraction=exit_fraction,
        computed_size_usd=computed_size_usd,
    )


def poll_once(conn, data_client: DataAPIClient, config) -> None:
    """One pass over every tracked wallet's recent activity. Shared by the
    always-on loop (run()) and the single-shot cron entrypoint."""
    for wallet in config.tracked_wallets:
        activity = data_client.get_activity(wallet, limit=50)
        for item in reversed(activity):  # oldest first, preserves ledger order
            signal_id = process_activity_item(conn, wallet, item, config.bankroll_usd, data_client)
            if signal_id:
                logger.info("new signal id=%s wallet=%s token=%s", signal_id, wallet, item.get("asset"))


def run():
    """Continuous loop for always-on deployments (e.g. run_both.py). For a
    scheduled/cron deployment, use run_cron.py instead -- it calls
    poll_once() a single time per invocation."""
    config = load_config()
    conn = db.get_connection(config.db_path)
    data_client = DataAPIClient(config.data_api_base)

    for wallet in config.tracked_wallets:
        seed_wallet(conn, data_client, wallet)

    logger.info(
        "Psylocke 1 (signal bot) watching %d wallet(s), poll every %ss",
        len(config.tracked_wallets),
        config.poll_interval_seconds,
    )
    poll_forever(lambda: poll_once(conn, data_client, config), config.poll_interval_seconds, logger)


if __name__ == "__main__":
    run()
