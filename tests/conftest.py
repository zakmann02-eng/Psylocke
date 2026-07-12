import os

import pytest

os.environ.setdefault("TRACKED_WALLETS", "0xabc")

from psylocke import db  # noqa: E402
from psylocke.config import Config  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(str(tmp_path / "test.db"))
    yield connection
    connection.close()


@pytest.fixture
def config(tmp_path):
    return Config(
        tracked_wallets=["0xabc"],
        bankroll_usd=1000.0,
        dry_run=True,
        poll_interval_seconds=1,
        execution_delay_seconds=0,
        max_position_usd=0,
        min_trade_usd=1,
        db_path=str(tmp_path / "test.db"),
        # Most tests aren't exercising the US-market filter; keep it off so
        # they don't need a fake Gamma API response. Filter-specific tests
        # override this with dataclasses.replace(...).
        require_us_markets=False,
        telegram_bot_token="",
        telegram_chat_id="",
    )
