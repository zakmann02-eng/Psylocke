import time

from psylocke import db


def _insert(conn, tx_hash, status, created_at):
    return db.insert_signal(
        conn,
        tx_hash=tx_hash,
        wallet="0xabc",
        token_id="tok",
        kind="ENTRY",
        tracked_side="BUY",
        tracked_price=0.5,
        tracked_size_usd=10.0,
        status=status,
        created_at=created_at,
    )


def test_prune_old_records_keeps_pending_and_recent(conn):
    old = time.time() - 100 * 86400
    recent = time.time()

    _insert(conn, "a", "EXECUTED_DRYRUN", old)
    pending_old_id = _insert(conn, "b", "PENDING", old)
    recent_id = _insert(conn, "c", "EXECUTED_DRYRUN", recent)

    db.mark_seen(conn, "0xabc", "old-tx")
    conn.execute("UPDATE seen_activity SET seen_at = ? WHERE tx_hash = 'old-tx'", (old,))
    conn.commit()

    deleted = db.prune_old_records(conn, retention_days=30)

    assert deleted == 1
    remaining_ids = {row["id"] for row in conn.execute("SELECT id FROM signals").fetchall()}
    assert remaining_ids == {pending_old_id, recent_id}
    assert conn.execute(
        "SELECT COUNT(*) c FROM seen_activity WHERE tx_hash = 'old-tx'"
    ).fetchone()["c"] == 0


def test_incremental_vacuum_and_checkpoint_do_not_error(conn):
    db.incremental_vacuum(conn)
    db.checkpoint(conn)
