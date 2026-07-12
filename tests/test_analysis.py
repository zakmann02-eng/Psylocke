from dataclasses import replace

from psylocke import db
from psylocke.analysis import is_us_market, process_activity_item, seed_wallet


class FakeDataClient:
    def __init__(self, positions=None, activity=None, portfolio_value=10_000.0, tags=None):
        self._positions = positions or []
        self._activity = activity or []
        self._portfolio_value = portfolio_value
        self._tags = tags if tags is not None else []

    def get_positions(self, wallet):
        return self._positions

    def get_activity(self, wallet, limit=100):
        return self._activity

    def get_portfolio_value(self, wallet):
        return self._portfolio_value

    def get_market_tags(self, condition_id):
        return self._tags


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


def test_entry_signal_computes_proportional_size(conn, config):
    client = FakeDataClient(portfolio_value=10_000.0)
    item = make_trade("tx2", "BUY", price=0.5, size=200.0)  # $100 trade

    signal_id = process_activity_item(conn, "0xabc", item, config, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["kind"] == "ENTRY"
    # bankroll/wallet_value ratio = 1000/10000 = 0.1 -> 0.1 * $100 = $10
    assert row["computed_size_usd"] == 10.0
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 200.0


def test_duplicate_activity_is_ignored(conn, config):
    client = FakeDataClient()
    item = make_trade("tx3", "BUY")
    first = process_activity_item(conn, "0xabc", item, config, data_client=client)
    second = process_activity_item(conn, "0xabc", item, config, data_client=client)

    assert first is not None
    assert second is None
    assert conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 1


def test_partial_exit_fraction(conn, config):
    client = FakeDataClient()
    entry = make_trade("tx4", "BUY", price=0.5, size=100.0)
    process_activity_item(conn, "0xabc", entry, config, data_client=client)

    exit_ = make_trade("tx5", "SELL", price=0.6, size=25.0)
    signal_id = process_activity_item(conn, "0xabc", exit_, config, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["kind"] == "EXIT"
    assert row["exit_fraction"] == 0.25
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 75.0


def test_full_exit_fraction_caps_at_one(conn, config):
    client = FakeDataClient()
    entry = make_trade("tx6", "BUY", price=0.5, size=50.0)
    process_activity_item(conn, "0xabc", entry, config, data_client=client)

    # tracked wallet sells more than we recorded them holding (e.g. we missed
    # some history) -- fraction should still cap at 1.0, not overshoot.
    exit_ = make_trade("tx7", "SELL", price=0.6, size=80.0)
    signal_id = process_activity_item(conn, "0xabc", exit_, config, data_client=client)

    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    assert row["exit_fraction"] == 1.0
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 0.0


def test_us_market_filter_allows_tagged_market(conn, config):
    us_config = replace(config, require_us_markets=True, us_market_tags=frozenset({"politics"}))
    # get_market_tags always returns pre-lowercased tags (see
    # polymarket_data.py), so the fake mirrors that contract.
    client = FakeDataClient(tags=["politics"])
    item = make_trade("tx8", "BUY")

    signal_id = process_activity_item(conn, "0xabc", item, us_config, data_client=client)

    assert signal_id is not None
    assert db.get_cached_market_tags(conn, "cond-1") == ["politics"]


def test_us_market_filter_blocks_untagged_market(conn, config):
    us_config = replace(config, require_us_markets=True, us_market_tags=frozenset({"politics"}))
    client = FakeDataClient(tags=["crypto"])
    item = make_trade("tx9", "BUY")

    signal_id = process_activity_item(conn, "0xabc", item, us_config, data_client=client)

    assert signal_id is None
    assert conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0
    # ledger still updates even though the signal was filtered out
    assert db.get_wallet_shares(conn, "0xabc", "tok-1") == 100.0


def test_us_market_filter_fails_closed_on_lookup_error(conn, config):
    us_config = replace(config, require_us_markets=True, us_market_tags=frozenset({"politics"}))

    class BrokenDataClient(FakeDataClient):
        def get_market_tags(self, condition_id):
            raise RuntimeError("boom")

    client = BrokenDataClient()
    assert is_us_market(conn, client, us_config, "cond-1") is False


def test_market_tags_are_cached(conn, config):
    us_config = replace(config, require_us_markets=True, us_market_tags=frozenset({"politics"}))

    class CountingDataClient(FakeDataClient):
        def __init__(self):
            super().__init__(tags=["politics"])
            self.calls = 0

        def get_market_tags(self, condition_id):
            self.calls += 1
            return super().get_market_tags(condition_id)

    client = CountingDataClient()
    assert is_us_market(conn, client, us_config, "cond-1") is True
    assert is_us_market(conn, client, us_config, "cond-1") is True
    assert client.calls == 1
