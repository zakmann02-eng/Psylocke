from dataclasses import replace

from psylocke import db
from psylocke.execution_bot import execute_entry, execute_exit


class FakeExecutionClient:
    def __init__(self):
        self.orders = []

    def place_order(self, token_id, side, price, size_shares):
        self.orders.append((token_id, side, price, size_shares))
        return {"status": "ok"}


def insert_entry_signal(conn, **overrides):
    fields = dict(
        tx_hash="tx1",
        wallet="0xabc",
        market_id="cond-1",
        token_id="tok-1",
        outcome="Yes",
        title="Some market",
        kind="ENTRY",
        tracked_side="BUY",
        tracked_price=0.5,
        tracked_size_usd=100.0,
        tracked_wallet_value_usd=10_000.0,
        computed_size_usd=10.0,
    )
    fields.update(overrides)
    signal_id = db.insert_signal(conn, **fields)
    return conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()


def insert_exit_signal(conn, exit_fraction, **overrides):
    fields = dict(
        tx_hash="tx2",
        wallet="0xabc",
        market_id="cond-1",
        token_id="tok-1",
        outcome="Yes",
        title="Some market",
        kind="EXIT",
        tracked_side="SELL",
        tracked_price=0.6,
        tracked_size_usd=50.0,
        exit_fraction=exit_fraction,
    )
    fields.update(overrides)
    signal_id = db.insert_signal(conn, **fields)
    return conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()


def test_dry_run_entry_records_position_without_placing_order(conn, config):
    client = FakeExecutionClient()
    signal = insert_entry_signal(conn)

    execute_entry(conn, client, config, signal)

    assert client.orders == []
    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal["id"],)).fetchone()["status"]
    assert status == "EXECUTED_DRYRUN"
    pos = db.get_own_position(conn, "tok-1")
    assert pos["shares"] == 20.0  # $10 / 0.5


def test_max_position_cap_is_applied(conn, config):
    capped_config = replace(config, max_position_usd=5.0)
    client = FakeExecutionClient()
    signal = insert_entry_signal(conn)

    execute_entry(conn, client, capped_config, signal)

    pos = db.get_own_position(conn, "tok-1")
    assert pos["shares"] == 10.0  # $5 cap / 0.5 price


def test_entry_below_min_trade_is_skipped(conn, config):
    tiny_config = replace(config, min_trade_usd=50.0)
    client = FakeExecutionClient()
    signal = insert_entry_signal(conn)

    execute_entry(conn, client, tiny_config, signal)

    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal["id"],)).fetchone()["status"]
    assert status == "SKIPPED"
    assert db.get_own_position(conn, "tok-1") is None


def test_exit_mirrors_fraction_of_own_position(conn, config):
    client = FakeExecutionClient()
    db.upsert_own_position(conn, "tok-1", "cond-1", shares=40.0, avg_price=0.5, source_wallet="0xabc")

    signal = insert_exit_signal(conn, exit_fraction=0.5)
    execute_exit(conn, client, config, signal)

    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal["id"],)).fetchone()["status"]
    assert status == "EXECUTED_DRYRUN"
    pos = db.get_own_position(conn, "tok-1")
    assert pos["shares"] == 20.0


def test_exit_with_no_open_position_is_skipped(conn, config):
    client = FakeExecutionClient()
    signal = insert_exit_signal(conn, exit_fraction=1.0)

    execute_exit(conn, client, config, signal)

    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal["id"],)).fetchone()["status"]
    assert status == "SKIPPED"


def test_live_mode_places_real_order(conn, config):
    live_config = replace(config, dry_run=False)
    client = FakeExecutionClient()
    signal = insert_entry_signal(conn)

    execute_entry(conn, client, live_config, signal)

    assert client.orders == [("tok-1", "BUY", 0.5, 20.0)]
    status = conn.execute("SELECT status FROM signals WHERE id = ?", (signal["id"],)).fetchone()["status"]
    assert status == "EXECUTED"
