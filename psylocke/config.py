"""Environment-driven configuration shared by both bots."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _get_wallets() -> list:
    raw = os.getenv("TRACKED_WALLETS", "")
    wallets = [w.strip().lower() for w in raw.split(",") if w.strip()]
    if not wallets:
        raise RuntimeError(
            "TRACKED_WALLETS is not set. Add at least one wallet address to .env"
        )
    return wallets


# Best-effort default -- Polymarket's actual tag taxonomy hasn't been
# verified against a live response (see polymarket_data.py). Check real
# tag values for a few markets you care about and adjust US_MARKET_TAGS
# before relying on this filter.
DEFAULT_US_MARKET_TAGS = (
    "politics,us-politics,us-current-affairs,us-election,elections,"
    "congress,supreme-court,fed,federal-reserve,us-economy,economy"
)


def _get_tag_set(name: str, default_csv: str) -> frozenset:
    raw = os.getenv(name, default_csv)
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


@dataclass(frozen=True)
class Config:
    tracked_wallets: list = field(default_factory=_get_wallets)
    bankroll_usd: float = field(default_factory=lambda: _get_float("BANKROLL_USD", 0.0))
    dry_run: bool = field(default_factory=lambda: _get_bool("DRY_RUN", True))
    poll_interval_seconds: float = field(
        default_factory=lambda: _get_float("POLL_INTERVAL_SECONDS", 30)
    )
    # Signals sit for this long before the bot will act on them. This is a
    # deliberate buffer (not a performance knob): acting instantly on a
    # detected trade is what makes a copy-trading bot conspicuous.
    execution_delay_seconds: float = field(
        default_factory=lambda: _get_float("EXECUTION_DELAY_SECONDS", 20)
    )
    # 0 disables the cap; otherwise no single entry order will exceed this.
    max_position_usd: float = field(
        default_factory=lambda: _get_float("MAX_POSITION_USD", 0)
    )
    min_trade_usd: float = field(default_factory=lambda: _get_float("MIN_TRADE_USD", 1))
    # How long to keep resolved signals/dedup records before pruning them,
    # so the sqlite file (and the volume it lives on) doesn't grow forever.
    retention_days: float = field(default_factory=lambda: _get_float("RETENTION_DAYS", 30))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/psylocke.db"))
    data_api_base: str = field(
        default_factory=lambda: os.getenv("DATA_API_BASE", "https://data-api.polymarket.com")
    )
    clob_api_base: str = field(
        default_factory=lambda: os.getenv("CLOB_API_BASE", "https://clob.polymarket.com")
    )
    poly_private_key: str = field(default_factory=lambda: os.getenv("POLY_PRIVATE_KEY", ""))
    poly_funder_address: str = field(
        default_factory=lambda: os.getenv("POLY_FUNDER_ADDRESS", "")
    )
    poly_chain_id: int = field(default_factory=lambda: int(os.getenv("POLY_CHAIN_ID", "137")))
    # Which of Polymarket's order-signing schemes to use -- must match how
    # the trading account logs in, or orders will settle against the wrong
    # address. 0 = EOA (raw private key, no proxy), 1 = POLY_PROXY (email /
    # magic-link login), 2 = POLY_GNOSIS_SAFE (browser wallet, e.g.
    # MetaMask -- Polymarket's default for most human accounts).
    poly_signature_type: int = field(
        default_factory=lambda: int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
    )
    gamma_api_base: str = field(
        default_factory=lambda: os.getenv("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
    )
    # If true, only signal on markets tagged with one of us_market_tags; a
    # market whose tags can't be determined is skipped (fail closed) rather
    # than risk copying a non-US market.
    require_us_markets: bool = field(
        default_factory=lambda: _get_bool("REQUIRE_US_MARKETS", True)
    )
    us_market_tags: frozenset = field(
        default_factory=lambda: _get_tag_set("US_MARKET_TAGS", DEFAULT_US_MARKET_TAGS)
    )
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


def load_config() -> Config:
    return Config()
