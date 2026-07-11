from psylocke import db
from psylocke.signal_bot import process_activity_item, seed_wallet


class FakeDataClient:
    def __init__(self, positions=None, activity=None, portfolio_value=10_000.0):
        self._positions = positions or []
        self._activity = activity or []
        self._portfolio_value = portfolio_value

    def get_positions(self, wallet):
        return self._positions

    def get_activity(self, wallet, limit=100):
        return self._activity

    def get_portfolio_value(self, wallet):
        return self._portfolio_value


def make_trade(tx_hash, side, price=0.5, size=100.0, token_id="tok-1", usd_size=None):
    return {
        "transactionHash": tx_hash,
        "asset": token_id,
        "conditionId": "cond-1",
        "side": side,
        "price": price,
        "size": size,
        "usdcSize": usd_size if usd_size is not None else size * price,
        "title": "Some market",
        "outcome": "Yes",
    }


def test_seed_wallet_marks_history_without_creating_signals(conn):
    client = FakeDataClient(
        positions=[{"asset": "tok-1", "size": 40.0}],
        activity=[make_trade("tx1", "BUY")],
    )
    seed_wallet(conn, client, "0xabc")

    assert db.is_wallet_seeded(conn, "0xabc")
    assert db.has_seen(conn, "0xabc", "tx1")
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 40.0
    assert conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0

    # seeding is idempotent
    seed_wallet(conn, client, "0xabc")
    assert conn.execute("SELECT COUNT(*) c FROM wallet_seeded").fetchone()["c"] == 1


def test_entry_signal_computes_proportional_size(conn):
    client = FakeDataClient(portfolio_value=10_000.0)
    item = make_trade("tx2", "BUY", price=0.5, size=200.0)  # $100 trade

    signal_id = process_activity_item(conn, "0xabc", item, bankroll_usd=1000.0, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["kind"] == "ENTRY"
    # bankroll/wallet_value ratio = 1000/10000 = 0.1 -> 0.1 * $100 = $10
    assert row["computed_size_usd"] == 10.0
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 200.0


def test_duplicate_activity_is_ignored(conn):
    client = FakeDataClient()
    item = make_trade("tx3", "BUY")
    first = process_activity_item(conn, "0xabc", item, bankroll_usd=1000.0, data_client=client)
    second = process_activity_item(conn, "0xabc", item, bankroll_usd=1000.0, data_client=client)

    assert first is not None
    assert second is None
    assert conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 1


def test_partial_exit_fraction(conn):
    client = FakeDataClient()
    entry = make_trade("tx4", "BUY", price=0.5, size=100.0)
    process_activity_item(conn, "0xabc", entry, bankroll_usd=1000.0, data_client=client)

    exit_ = make_trade("tx5", "SELL", price=0.6, size=25.0)
    signal_id = process_activity_item(conn, "0xabc", exit_, bankroll_usd=1000.0, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["kind"] == "EXIT"
    assert row["exit_fraction"] == 0.25
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 75.0


def test_full_exit_fraction_caps_at_one(conn):
    client = FakeDataClient()
    entry = make_trade("tx6", "BUY", price=0.5, size=50.0)
    process_activity_item(conn, "0xabc", entry, bankroll_usd=1000.0, data_client=client)

    # tracked wallet sells more than we recorded them holding (e.g. we missed
    # some history) -- fraction should still cap at 1.0, not overshoot.
    exit_ = make_trade("tx7", "SELL", price=0.6, size=80.0)
    signal_id = process_activity_item(conn, "0xabc", exit_, bankroll_usd=1000.0, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["exit_fraction"] == 1.0
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 0.0
