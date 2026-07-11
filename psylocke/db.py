"""Shared SQLite store. Psylocke 1 (signal bot) and Psylocke 2 (execution
bot) are separate processes that never talk to each other directly — this
database file is the entire interface between them, so a signal written by
one is durable and auditable even if the other is down or restarts.
"""
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_activity (
    tx_hash TEXT NOT NULL,
    wallet TEXT NOT NULL,
    seeded INTEGER NOT NULL DEFAULT 0,
    seen_at REAL NOT NULL,
    PRIMARY KEY (tx_hash, wallet)
);

CREATE TABLE IF NOT EXISTS wallet_positions (
    wallet TEXT NOT NULL,
    token_id TEXT NOT NULL,
    shares REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (wallet, token_id)
);

CREATE TABLE IF NOT EXISTS wallet_seeded (
    wallet TEXT PRIMARY KEY,
    seeded_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash TEXT NOT NULL,
    wallet TEXT NOT NULL,
    market_id TEXT,
    token_id TEXT NOT NULL,
    outcome TEXT,
    title TEXT,
    kind TEXT NOT NULL,              -- ENTRY | EXIT
    tracked_side TEXT NOT NULL,      -- BUY | SELL
    tracked_price REAL NOT NULL,
    tracked_size_usd REAL NOT NULL,
    tracked_wallet_value_usd REAL,
    exit_fraction REAL,
    computed_size_usd REAL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    -- PENDING | EXECUTED | EXECUTED_DRYRUN | SKIPPED | FAILED
    notes TEXT,
    created_at REAL NOT NULL,
    executed_at REAL
);

CREATE TABLE IF NOT EXISTS own_positions (
    token_id TEXT PRIMARY KEY,
    market_id TEXT,
    shares REAL NOT NULL DEFAULT 0,
    avg_price REAL NOT NULL DEFAULT 0,
    source_wallet TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS market_tags (
    market_id TEXT PRIMARY KEY,
    tags TEXT NOT NULL,       -- comma-joined, lowercase
    fetched_at REAL NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # auto_vacuum only takes effect on table creation, so it must be set
    # before executescript() on a brand-new database file.
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def prune_old_records(conn: sqlite3.Connection, retention_days: float) -> int:
    """Delete signals/dedup records past their retention window so the
    database doesn't grow without bound. Never touches PENDING signals or
    the position ledgers, only the historical audit trail."""
    cutoff = time.time() - retention_days * 86400
    cur = conn.execute(
        "DELETE FROM signals WHERE created_at < ? AND status != 'PENDING'",
        (cutoff,),
    )
    deleted = cur.rowcount
    conn.execute("DELETE FROM seen_activity WHERE seen_at < ?", (cutoff,))
    conn.commit()
    return deleted


def incremental_vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA incremental_vacuum")
    conn.commit()


def checkpoint(conn: sqlite3.Connection) -> None:
    """Flush WAL contents into the main db file and truncate it. Matters
    most for short-lived (cron) processes -- without this the -wal file
    lingers and regrows every run instead of the main file staying small.
    """
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def is_wallet_seeded(conn: sqlite3.Connection, wallet: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM wallet_seeded WHERE wallet = ?", (wallet,)
    ).fetchone()
    return row is not None


def mark_wallet_seeded(conn: sqlite3.Connection, wallet: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO wallet_seeded (wallet, seeded_at) VALUES (?, ?)",
        (wallet, time.time()),
    )
    conn.commit()


def has_seen(conn: sqlite3.Connection, wallet: str, tx_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_activity WHERE wallet = ? AND tx_hash = ?",
        (wallet, tx_hash),
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, wallet: str, tx_hash: str, seeded: bool = False) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_activity (tx_hash, wallet, seeded, seen_at) "
        "VALUES (?, ?, ?, ?)",
        (tx_hash, wallet, int(seeded), time.time()),
    )
    conn.commit()


def get_wallet_shares(conn: sqlite3.Connection, wallet: str, token_id: str) -> float:
    row = conn.execute(
        "SELECT shares FROM wallet_positions WHERE wallet = ? AND token_id = ?",
        (wallet, token_id),
    ).fetchone()
    return row["shares"] if row else 0.0


def set_wallet_shares(conn: sqlite3.Connection, wallet: str, token_id: str, shares: float) -> None:
    conn.execute(
        "INSERT INTO wallet_positions (wallet, token_id, shares, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(wallet, token_id) DO UPDATE SET shares = excluded.shares, "
        "updated_at = excluded.updated_at",
        (wallet, token_id, shares, time.time()),
    )
    conn.commit()


def insert_signal(conn: sqlite3.Connection, **fields) -> int:
    fields.setdefault("created_at", time.time())
    fields.setdefault("status", "PENDING")
    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO signals ({columns}) VALUES ({placeholders})",
        tuple(fields.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_actionable_signals(conn: sqlite3.Connection, older_than_seconds: float):
    cutoff = time.time() - older_than_seconds
    return conn.execute(
        "SELECT * FROM signals WHERE status = 'PENDING' AND created_at <= ? "
        "ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()


def update_signal_status(conn: sqlite3.Connection, signal_id: int, status: str, notes: str = None) -> None:
    conn.execute(
        "UPDATE signals SET status = ?, notes = COALESCE(?, notes), executed_at = ? "
        "WHERE id = ?",
        (status, notes, time.time(), signal_id),
    )
    conn.commit()


def get_own_position(conn: sqlite3.Connection, token_id: str):
    return conn.execute(
        "SELECT * FROM own_positions WHERE token_id = ?", (token_id,)
    ).fetchone()


def upsert_own_position(
    conn: sqlite3.Connection,
    token_id: str,
    market_id: str,
    shares: float,
    avg_price: float,
    source_wallet: str,
) -> None:
    conn.execute(
        "INSERT INTO own_positions (token_id, market_id, shares, avg_price, source_wallet, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(token_id) DO UPDATE SET shares = excluded.shares, "
        "avg_price = excluded.avg_price, updated_at = excluded.updated_at",
        (token_id, market_id, shares, avg_price, source_wallet, time.time()),
    )
    conn.commit()


def get_cached_market_tags(conn: sqlite3.Connection, market_id: str):
    """Returns a list of tags, or None if this market has never been looked
    up (as opposed to [] which means "looked up, has no tags")."""
    row = conn.execute(
        "SELECT tags FROM market_tags WHERE market_id = ?", (market_id,)
    ).fetchone()
    if row is None:
        return None
    return [t for t in row["tags"].split(",") if t]


def set_market_tags(conn: sqlite3.Connection, market_id: str, tags) -> None:
    conn.execute(
        "INSERT INTO market_tags (market_id, tags, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT(market_id) DO UPDATE SET tags = excluded.tags, "
        "fetched_at = excluded.fetched_at",
        (market_id, ",".join(tags), time.time()),
    )
    conn.commit()
