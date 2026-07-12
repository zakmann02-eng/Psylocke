from psylocke import bot, db


class FakeDataClient:
    def __init__(self):
        self.activity = []

    def get_positions(self, wallet):
        return []

    def get_activity(self, wallet, limit=100):
        return self.activity

    def get_portfolio_value(self, wallet):
        return 1000.0


class FakeExecutionClient:
    def __init__(self):
        self.orders = []

    def place_order(self, *args, **kwargs):
        self.orders.append((args, kwargs))
        return {"status": "ok"}


def test_run_once_analyzes_and_executes_in_a_single_cycle(conn, config):
    # The `config` fixture sets execution_delay_seconds=0, so a signal
    # created during this cycle is immediately actionable within the same
    # cycle -- there's no second bot to hand off to, just one bot doing
    # both steps back to back.
    data_client = FakeDataClient()
    data_client.activity = [
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
    execution_client = FakeExecutionClient()

    bot.run_once(conn, data_client, execution_client, config)

    row = conn.execute("SELECT * FROM signals WHERE tx_hash = 'tx1'").fetchone()
    assert row is not None
    assert row["status"] == "EXECUTED_DRYRUN"  # config fixture defaults dry_run=True
    pos = db.get_own_position(conn, "tok-1")
    assert pos is not None
    assert pos["shares"] > 0


def test_run_once_skips_execution_for_signals_still_within_delay(conn, config):
    from dataclasses import replace

    delayed_config = replace(config, execution_delay_seconds=999)
    data_client = FakeDataClient()
    data_client.activity = [
        {
            "transactionHash": "tx2",
            "asset": "tok-2",
            "conditionId": "cond-2",
            "side": "BUY",
            "price": 0.5,
            "size": 100.0,
            "usdcSize": 50.0,
            "title": "Some market",
            "outcome": "Yes",
        }
    ]
    execution_client = FakeExecutionClient()

    bot.run_once(conn, data_client, execution_client, delayed_config)

    row = conn.execute("SELECT * FROM signals WHERE tx_hash = 'tx2'").fetchone()
    assert row["status"] == "PENDING"
    assert db.get_own_position(conn, "tok-2") is None
