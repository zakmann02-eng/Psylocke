import run_cron

from psylocke import db


class FakeDataClient:
    def __init__(self, *_args, **_kwargs):
        self.activity = []

    def get_positions(self, wallet):
        return []

    def get_activity(self, wallet, limit=100):
        return self.activity

    def get_portfolio_value(self, wallet):
        return 1000.0


class FakeExecutionClient:
    def __init__(self, config):
        self.orders = []

    def place_order(self, *args, **kwargs):
        self.orders.append((args, kwargs))
        return {"status": "ok"}


def test_run_cron_main_seeds_signals_and_defers_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKED_WALLETS", "0xabc")
    monkeypatch.setenv("BANKROLL_USD", "1000")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cron.db"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EXECUTION_DELAY_SECONDS", "20")

    fake_data_client = FakeDataClient()
    monkeypatch.setattr(run_cron, "DataAPIClient", lambda *a, **kw: fake_data_client)
    monkeypatch.setattr(run_cron, "ExecutionClient", FakeExecutionClient)

    # First cron pass just seeds the wallet (no history yet); nothing to do.
    run_cron.main()

    fake_data_client.activity = [
        {
            "transactionHash": "tx1",
            "asset": "tok-1",
            "conditionId": "cond-1",
            "side": "BUY",
            "price": 0.5,
            "size": 100.0,
            "usdcSize": 50.0,
            "title": "Some market",
            "outcome": "Yes",
        }
    ]

    # Second pass: a new trade appears -> a signal is created but not yet
    # executed, since it's younger than EXECUTION_DELAY_SECONDS. This is
    # what makes copying safe to run once every cron cycle: entry and exit
    # never happen within the same invocation that created the signal.
    run_cron.main()

    conn = db.get_connection(str(tmp_path / "cron.db"))
    row = conn.execute("SELECT * FROM signals WHERE tx_hash = 'tx1'").fetchone()
    conn.close()

    assert row is not None
    assert row["kind"] == "ENTRY"
    assert row["status"] == "PENDING"
