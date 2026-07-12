"""Analysis: turns tracked wallets' on-chain trades into internal signals.

This is one half of what the bot does each cycle (see bot.py) -- it watches
the tracked wallets' public activity and decides what should be copied. It
never places an order itself; execution.py reads what this writes and acts
on it after a deliberate delay (see EXECUTION_DELAY_SECONDS). Splitting
detection from action isn't about separate processes anymore, it's about
keeping "what should we do" and "actually spending money" as distinct,
independently reviewable steps within the one bot.
"""
from . import db, notify
from .logging_setup import setup_logging
from .polymarket_data import DataAPIClient

logger = setup_logging("psylocke.analysis")


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


def is_us_market(conn, data_client: DataAPIClient, config, market_id: str) -> bool:
    """Fail-closed tag check: a market only passes if we positively confirm
    one of its tags is in config.us_market_tags. If we can't determine its
    tags at all (lookup error, unknown market), it's treated as non-US
    rather than risk copying a trade outside the requested scope."""
    if not market_id:
        return False
    tags = db.get_cached_market_tags(conn, market_id)
    if tags is None:
        try:
            tags = data_client.get_market_tags(market_id)
        except Exception:
            logger.exception("failed to fetch tags for market %s; treating as non-US", market_id)
            return False
        db.set_market_tags(conn, market_id, tags)
    return any(tag in config.us_market_tags for tag in tags)


def process_activity_item(conn, wallet: str, item: dict, config, data_client: DataAPIClient = None):
    """Turn one raw activity item into a signal row, if it's new and (when
    REQUIRE_US_MARKETS is set) about a US-tagged market. Returns the new
    signal id, or None if this trade was already processed or filtered
    out -- the wallet's position ledger is still updated either way, so
    later exit-fraction math stays correct regardless of the filter."""
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
        if wallet_value and wallet_value > 0 and config.bankroll_usd > 0:
            computed_size_usd = (config.bankroll_usd / wallet_value) * trade["usd_size"]
    else:
        kind = "EXIT"
        db.set_wallet_shares(conn, wallet, trade["token_id"], max(prior_shares - trade["shares"], 0.0))
        exit_fraction = min(trade["shares"] / prior_shares, 1.0) if prior_shares > 0 else 1.0

    if config.require_us_markets and data_client is not None:
        if not is_us_market(conn, data_client, config, trade["market_id"]):
            logger.info(
                "skipping non-US (or unverifiable) market: %s (%s)",
                trade["market_id"], trade["title"],
            )
            return None

    signal_id = db.insert_signal(
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

    if kind == "ENTRY":
        size_note = f"${computed_size_usd:.2f}" if computed_size_usd else "unknown (bankroll or wallet value not set)"
        notify.send(
            config,
            f"\U0001F50D <b>Psylocke</b> — new ENTRY signal #{signal_id}\n"
            f"{trade['title']} ({trade['outcome']})\n"
            f"Wallet {wallet} bought ${trade['usd_size']:.2f} @ {trade['price']:.3f}\n"
            f"Proportional size: {size_note}",
        )
    else:
        notify.send(
            config,
            f"\U0001F50D <b>Psylocke</b> — new EXIT signal #{signal_id}\n"
            f"{trade['title']} ({trade['outcome']})\n"
            f"Wallet {wallet} closed {exit_fraction * 100:.0f}% of their position @ {trade['price']:.3f}",
        )

    return signal_id


def poll_once(conn, data_client: DataAPIClient, config) -> None:
    """One analysis pass over every tracked wallet's recent activity."""
    for wallet in config.tracked_wallets:
        activity = data_client.get_activity(wallet, limit=50)
        for item in reversed(activity):  # oldest first, preserves ledger order
            signal_id = process_activity_item(conn, wallet, item, config, data_client)
            if signal_id:
                logger.info("new signal id=%s wallet=%s token=%s", signal_id, wallet, item.get("asset"))
