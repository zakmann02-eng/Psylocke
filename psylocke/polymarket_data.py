"""Read-only client for Polymarket's public Data API.

Field names below match Polymarket's documented Data API responses
(activity/positions/value endpoints) as of this writing. This project was
built in a sandbox with no network access to data-api.polymarket.com, so
these mappings have not been exercised against a live response — verify
them against a real payload (e.g. `curl` the endpoints yourself) before
trusting DRY_RUN output.
"""
import logging
import time

import requests
from requests.adapters import HTTPAdapter, Retry

logger = logging.getLogger("psylocke.data_api")


class DataAPIClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _get(self, path: str, params: dict) -> list:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_activity(self, wallet: str, limit: int = 100) -> list:
        """Most recent on-chain trade activity for a wallet, newest first."""
        data = self._get(
            "/activity",
            {
                "user": wallet,
                "limit": limit,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
        )
        return [item for item in data if item.get("type") == "TRADE"]

    def get_positions(self, wallet: str) -> list:
        return self._get("/positions", {"user": wallet})

    def get_portfolio_value(self, wallet: str) -> float:
        data = self._get("/value", {"user": wallet})
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0.0))
        if isinstance(data, dict):
            return float(data.get("value", 0.0))
        return 0.0


def poll_forever(fn, interval_seconds: float, logger_: logging.Logger = logger):
    while True:
        try:
            fn()
        except Exception:
            logger_.exception("poll iteration failed")
        time.sleep(interval_seconds)
