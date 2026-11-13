"""公告 SQLite 批次存储单元测试：完整批次替换、失败批次隔离、不叠加旧记录。"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from commuter.notice_store import (
    connect,
    get_latest_complete,
    get_latest_failure,
    save_complete_batch,
    save_failed_batch,
)
from commuter.notices import Notice, parse_notice_date


def notice(no, trip="N201", start="2026-11-10", end="2026-11-20",
           url="http://x/notices/page-1"):
    return Notice(
        notice_no=no, trip_no=trip,
        start_date=parse_notice_date(start), end_date=parse_notice_date(end),
        page_url=url, raw_start=start, raw_end=end,
    )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "notices.sqlite3"
        self.source = "http://x/notices/"

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_batch_saved_and_read(self):
        with connect(self.db) as conn:
            batch_id = save_complete_batch(conn, self.source, [notice("GG-1")], page_count=2)
            batch, notices = get_latest_complete(conn, self.source)
        self.assertEqual(batch["id"], batch_id)
        self.assertEqual(batch["status"], "complete")
        self.assertEqual(batch["page_count"], 2)
        self.assertEqual(batch["notice_count"], 1)
        self.assertEqual(notices[0].notice_no, "GG-1")
        self.assertEqual(notices[0].start_date, date(2026, 11, 10))

    def test_requery_replaces_instead_of_accumulating(self):
        with connect(self.db) as conn:
            save_complete_batch(conn, self.source,
                                [notice("GG-1"), notice("GG-2")], page_count=2)
            save_complete_batch(conn, self.source,
                                [notice("GG-3")], page_count=3)
            batch, notices = get_latest_complete(conn, self.source)
            record_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notice_records WHERE source_url = ?",
                (self.source,),
            ).fetchone()["c"]
            batch_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notice_batches WHERE source_url = ?",
                (self.source,),
            ).fetchone()["c"]
        # 旧记录被整体替换，不叠加
        self.assertEqual([n.notice_no for n in notices], ["GG-3"])
        self.assertEqual(record_count, 1)
        self.assertEqual(batch_count, 1)
        self.assertEqual(batch["page_count"], 3)

    def test_failed_batch_keeps_old_complete_and_no_partial_records(self):
        with connect(self.db) as conn:
            save_complete_batch(conn, self.source, [notice("GG-OLD")], page_count=2)
            save_failed_batch(
                conn, self.source,
                errors=["第 2 页抓取失败"], failed_page_url="http://x/notices/page-2",
            )
            batch, notices = get_latest_complete(conn, self.source)
            failure = get_latest_failure(conn, self.source)
            record_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notice_records WHERE source_url = ?",
                (self.source,),
            ).fetchone()["c"]
        # 旧完整批次原样保留，失败不写入任何公告明细
        self.assertEqual([n.notice_no for n in notices], ["GG-OLD"])
        self.assertEqual(record_count, 1)
        self.assertIsNotNone(failure)
        self.assertEqual(failure["failed_page_url"], "http://x/notices/page-2")
        self.assertIn("第 2 页抓取失败", failure["error_text"])

    def test_failure_then_success_replaces_everything(self):
        with connect(self.db) as conn:
            save_complete_batch(conn, self.source, [notice("GG-OLD")], page_count=2)
            save_failed_batch(conn, self.source, errors=["boom"],
                              failed_page_url="http://x/page-2")
            save_complete_batch(conn, self.source, [notice("GG-NEW")], page_count=4)
            batch, notices = get_latest_complete(conn, self.source)
            self.assertIsNone(get_latest_failure(conn, self.source))
            record_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notice_records WHERE source_url = ?",
                (self.source,),
            ).fetchone()["c"]
        self.assertEqual([n.notice_no for n in notices], ["GG-NEW"])
        self.assertEqual(record_count, 1)
        self.assertEqual(batch["page_count"], 4)

    def test_sources_isolated(self):
        other = "http://y/notices/"
        with connect(self.db) as conn:
            save_complete_batch(conn, self.source, [notice("GG-X")], page_count=1)
            save_complete_batch(conn, other, [notice("GG-Y")], page_count=1)
            _, x_notices = get_latest_complete(conn, self.source)
            _, y_notices = get_latest_complete(conn, other)
        self.assertEqual([n.notice_no for n in x_notices], ["GG-X"])
        self.assertEqual([n.notice_no for n in y_notices], ["GG-Y"])

    def test_never_succeeded_has_no_complete_batch(self):
        with connect(self.db) as conn:
            save_failed_batch(conn, self.source, errors=["首页打不开"],
                              failed_page_url=self.source)
            batch, notices = get_latest_complete(conn, self.source)
        self.assertIsNone(batch)
        self.assertEqual(notices, [])

    def test_only_latest_failure_kept(self):
        with connect(self.db) as conn:
            save_failed_batch(conn, self.source, errors=["first"],
                              failed_page_url="http://x/page-2")
            save_failed_batch(conn, self.source, errors=["second"],
                              failed_page_url="http://x/page-3")
            failure = get_latest_failure(conn, self.source)
            failed_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notice_batches "
                "WHERE source_url = ? AND status = 'failed'",
                (self.source,),
            ).fetchone()["c"]
        self.assertIn("second", failure["error_text"])
        self.assertEqual(failed_count, 1)


if __name__ == "__main__":
    unittest.main()
