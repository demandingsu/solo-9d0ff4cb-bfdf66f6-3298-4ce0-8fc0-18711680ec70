"""停运公告端到端冒烟测试：示例站点 + Flask 测试客户端 + 临时 SQLite。

覆盖关键约束：
  * 完整抓取后按服务日列出停运车次与公告依据；
  * 重查同一来源替换旧批次、不叠加旧记录；
  * 断页时保留并标明上一次完整批次与失败页，部分新公告不混入；
  * 从未成功采集时不能断言“当日无停运”。
"""

import sys
import tempfile
import unittest
from pathlib import Path

import requests
from werkzeug.serving import make_server

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "example-site"
sys.path.insert(0, str(EXAMPLE_DIR))
import app as example_app  # noqa: E402

from commuter.app import app as commuter_app  # noqa: E402


class _Server:
    def __init__(self, flask_app, port):
        self.httpd = make_server("127.0.0.1", port, flask_app)
        self.thread = None

    def start(self):
        import threading

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()


class NoticeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server(example_app.app, 5061)
        cls.server.start()
        cls.healthy = "http://127.0.0.1:5061/notices/"
        cls.broken = "http://127.0.0.1:5061/notices-broken/"
        requests.get(cls.healthy, timeout=5).raise_for_status()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "notices.sqlite3")
        commuter_app.config["NOTICE_DB_PATH"] = self.db_path
        self.client = commuter_app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _post(self, url, day):
        return self.client.post(
            "/notices/", data={"home_url": url, "service_date": day}
        ).get_data(as_text=True)

    def test_full_scrape_lists_suspended_trips_with_basis(self):
        # 2026-11-13（周五）：GG-0001(N201)、GG-0002(N203)、GG-0004(X902)、GG-0007(X905) 生效
        html = self._post(self.healthy, "2026-11-13")
        self.assertIn("完整批次", html)
        self.assertIn("本次新采集", html)
        for text in ("GG-2026-0001", "GG-2026-0002", "GG-2026-0004", "GG-2026-0007",
                     "N201", "N203", "X902", "X905"):
            self.assertIn(text, html)
        # 当日不生效的公告不应出现在“当日停运”区（仍在全部公告区）
        # N204 生效区间 11-14 ~ 11-15
        self.assertIn("停运车次（共 4 条依据）", html)
        # 无法解析日期的 GG-0006 进入人工核对区
        self.assertIn("需人工核对", html)
        self.assertIn("GG-2026-0006", html)

    def test_day_without_suspension_says_so_only_after_success(self):
        # 2026-01-01 没有任何公告生效
        html = self._post(self.healthy, "2026-01-01")
        self.assertIn("没有生效中的停运公告", html)

    def test_requery_replaces_batch_without_accumulation(self):
        self._post(self.healthy, "2026-11-13")
        self._post(self.healthy, "2026-11-13")
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        try:
            batch_count = conn.execute(
                "SELECT COUNT(*) FROM notice_batches WHERE status = 'complete'"
            ).fetchone()[0]
            record_count = conn.execute("SELECT COUNT(*) FROM notice_records").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(batch_count, 1, "重查不得叠加批次")
        self.assertEqual(record_count, 7, "每批 7 条公告，旧明细应被替换")

    def test_broken_source_first_time_cannot_claim_no_suspension(self):
        html = self._post(self.broken, "2026-11-13")
        self.assertIn("本次公告抓取不完整", html)
        self.assertIn("从未成功采集", html)
        self.assertIn("不能据此判断当日无停运", html)
        self.assertIn("/notices-broken/page-2", html)
        # 失败不写入任何公告明细
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notice_records").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT status FROM notice_batches").fetchone()[0], "failed"
            )
        finally:
            conn.close()

    def test_broken_refetch_keeps_old_complete_batch_and_marks_failure(self):
        from commuter import notice_scraper

        # 先用健康来源建立完整批次
        html_ok = self._post(self.healthy, "2026-11-13")
        self.assertIn("完整批次", html_ok)

        # 再次抓取同一来源时让第 2 页失败：旧批次必须原样保留
        original_fetch = notice_scraper._fetch

        def flaky_fetch(session, url):
            if url.endswith("/notices/page-2"):
                raise requests.ConnectionError("模拟分页故障")
            return original_fetch(session, url)

        notice_scraper._fetch = flaky_fetch
        try:
            html_flaky = self._post(self.healthy, "2026-11-13")
        finally:
            notice_scraper._fetch = original_fetch

        self.assertIn("本次公告抓取不完整", html_flaky)
        self.assertIn("上一次完整批次", html_flaky)
        self.assertIn("/notices/page-2", html_flaky)
        # 旧批次仍是 7 条完整公告，没有混入部分数据
        self.assertIn("公告条数</th><td>7", html_flaky)
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        try:
            record_count = conn.execute(
                "SELECT COUNT(*) FROM notice_records WHERE source_url = ?", (self.healthy,)
            ).fetchone()[0]
            complete_batches = conn.execute(
                "SELECT COUNT(*) FROM notice_batches "
                "WHERE source_url = ? AND status = 'complete'",
                (self.healthy,),
            ).fetchone()[0]
            failed_batches = conn.execute(
                "SELECT COUNT(*) FROM notice_batches "
                "WHERE source_url = ? AND status = 'failed'",
                (self.healthy,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(record_count, 7)
        self.assertEqual(complete_batches, 1)
        self.assertEqual(failed_batches, 1)

    def test_form_validation(self):
        html = self.client.post("/notices/", data={}).get_data(as_text=True)
        self.assertIn("请填写公告首页 URL", html)
        self.assertIn("服务日期格式无效", html)

    def test_get_page_renders_form(self):
        html = self.client.get("/notices/").get_data(as_text=True)
        self.assertIn("停运公告查询", html)
        self.assertIn("公告首页 URL", html)


if __name__ == "__main__":
    unittest.main()
