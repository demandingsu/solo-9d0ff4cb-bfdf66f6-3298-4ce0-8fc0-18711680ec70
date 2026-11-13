"""停运公告的 SQLite 批次存储。

按公告来源（首页 URL）保存抓取批次：

* **完整批次**（``complete``）：全部分页取齐后写入，写入成功才在同一事务里
  删除该来源旧批次的公告与批次记录，保证“重查同一来源不叠加旧记录”；
* **失败批次**（``failed``）：只记录采集时间、失败页与错误信息，不写公告明细，
  也不触碰旧的完整批次，因此部分新公告永远不会混入旧结果。

每个来源最多保留一条完整批次与一条最近失败批次。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .notices import Notice

SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'failed')),
    fetched_at TEXT NOT NULL,
    page_count INTEGER,
    notice_count INTEGER,
    failed_page_url TEXT,
    error_text TEXT
);
CREATE TABLE IF NOT EXISTS notice_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    batch_id INTEGER NOT NULL,
    notice_no TEXT NOT NULL,
    trip_no TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    raw_start TEXT,
    raw_end TEXT,
    page_url TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES notice_batches (id)
);
CREATE INDEX IF NOT EXISTS idx_notice_batches_source
    ON notice_batches (source_url, status);
CREATE INDEX IF NOT EXISTS idx_notice_records_source
    ON notice_records (source_url);
"""

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "notices.sqlite3"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """打开（必要时创建并建表）公告数据库。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def save_complete_batch(
    conn: sqlite3.Connection,
    source_url: str,
    notices: list[Notice],
    *,
    page_count: int,
    fetched_at: datetime | None = None,
) -> int:
    """原子保存一次完整抓取，替换该来源的旧完整批次，返回新批次 id。

    旧批次（含旧失败记录与旧公告明细）与新批次在同一事务内完成替换：
    任一步骤失败则整体回滚，旧批次保持原样，不会出现新旧记录叠加。
    """
    fetched_at = fetched_at or datetime.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO notice_batches "
            "(source_url, status, fetched_at, page_count, notice_count) "
            "VALUES (?, 'complete', ?, ?, ?)",
            (source_url, fetched_at.isoformat(timespec="seconds"),
             page_count, len(notices)),
        )
        batch_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO notice_records "
            "(source_url, batch_id, notice_no, trip_no, start_date, end_date, "
            " raw_start, raw_end, page_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    source_url,
                    batch_id,
                    item.notice_no,
                    item.trip_no,
                    item.start_date.isoformat() if item.start_date else None,
                    item.end_date.isoformat() if item.end_date else None,
                    item.raw_start,
                    item.raw_end,
                    item.page_url,
                )
                for item in notices
            ],
        )
        # 新批次写好后再清理该来源的全部旧批次（含旧失败标记与旧明细）
        conn.execute(
            "DELETE FROM notice_records WHERE source_url = ? AND batch_id <> ?",
            (source_url, batch_id),
        )
        conn.execute(
            "DELETE FROM notice_batches WHERE source_url = ? AND id <> ?",
            (source_url, batch_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return batch_id


def save_failed_batch(
    conn: sqlite3.Connection,
    source_url: str,
    *,
    errors: list[str],
    failed_page_url: str | None = None,
    fetched_at: datetime | None = None,
) -> int:
    """记录一次失败抓取。

    只写批次标记与失败信息，不写任何公告明细，也不删除旧完整批次；
    同一来源只保留最近一条失败记录。
    """
    fetched_at = fetched_at or datetime.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO notice_batches "
            "(source_url, status, fetched_at, failed_page_url, error_text) "
            "VALUES (?, 'failed', ?, ?, ?)",
            (
                source_url,
                fetched_at.isoformat(timespec="seconds"),
                failed_page_url,
                "\n".join(errors),
            ),
        )
        failed_id = cur.lastrowid
        conn.execute(
            "DELETE FROM notice_batches "
            "WHERE source_url = ? AND status = 'failed' AND id <> ?",
            (source_url, failed_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return failed_id


def _row_to_notice(row: sqlite3.Row) -> Notice:
    return Notice(
        notice_no=row["notice_no"],
        trip_no=row["trip_no"],
        start_date=date.fromisoformat(row["start_date"]) if row["start_date"] else None,
        end_date=date.fromisoformat(row["end_date"]) if row["end_date"] else None,
        page_url=row["page_url"],
        raw_start=row["raw_start"] or "",
        raw_end=row["raw_end"] or "",
    )


def get_latest_complete(
    conn: sqlite3.Connection, source_url: str
) -> tuple[dict | None, list[Notice]]:
    """取该来源最近一次完整批次及其公告明细；从未成功时返回 ``(None, [])``。"""
    batch = conn.execute(
        "SELECT * FROM notice_batches WHERE source_url = ? AND status = 'complete' "
        "ORDER BY id DESC LIMIT 1",
        (source_url,),
    ).fetchone()
    if batch is None:
        return None, []
    rows = conn.execute(
        "SELECT * FROM notice_records WHERE batch_id = ? ORDER BY id",
        (batch["id"],),
    ).fetchall()
    return dict(batch), [_row_to_notice(row) for row in rows]


def get_latest_failure(conn: sqlite3.Connection, source_url: str) -> dict | None:
    """取该来源最近一次失败批次（含失败页与错误信息），没有则返回 ``None``。"""
    row = conn.execute(
        "SELECT * FROM notice_batches WHERE source_url = ? AND status = 'failed' "
        "ORDER BY id DESC LIMIT 1",
        (source_url,),
    ).fetchone()
    return dict(row) if row else None
