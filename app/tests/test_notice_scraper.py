"""公告抓取器单元测试：用本地静态 HTTP 服务模拟分页、断页与坏表头。"""

import functools
import http.server
import tempfile
import threading
import unittest
from pathlib import Path

from commuter.notice_scraper import scrape_notices

GOOD_PAGE_1 = """
<html><body><table>
<tr><th>公告编号</th><th>车次号</th><th>生效起始日期</th><th>生效截止日期</th></tr>
<tr><td>GG-1</td><td>N201</td><td>2026-11-10</td><td>2026-11-20</td></tr>
</table>
<a class="next-page" href="/page-2">下一页</a></body></html>
"""

GOOD_PAGE_2 = """
<html><body><table>
<tr><th>公告编号</th><th>车次号</th><th>生效起始日期</th><th>生效截止日期</th></tr>
<tr><td>GG-2</td><td>N203</td><td>2026年11月13日</td><td>2026年11月13日</td></tr>
<tr><td>GG-3</td><td>N202</td><td>即日</td><td>另行通知</td></tr>
</table>
</body></html>
"""

BAD_HEADER_PAGE = """
<html><body><table>
<tr><th>编号</th><th>车次</th></tr>
<tr><td>GG-9</td><td>N999</td></tr>
</table></body></html>
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 安静一点
        pass

    def do_GET(self):
        if self.path.startswith("/broken"):
            self.send_response(500)
            self.end_headers()
            return
        super().do_GET()


class ScraperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "index.html").write_text(GOOD_PAGE_1, encoding="utf-8")
        (root / "page-2").write_text(GOOD_PAGE_2, encoding="utf-8")
        (root / "bad.html").write_text(BAD_HEADER_PAGE, encoding="utf-8")

        handler = functools.partial(_QuietHandler, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.tmp.cleanup()

    def url(self, path="/index.html"):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_scrape_all_pages(self):
        notices, errors, failed_page, page_count = scrape_notices(self.url())
        self.assertEqual(errors, [])
        self.assertIsNone(failed_page)
        self.assertEqual(page_count, 2)
        self.assertEqual([n.notice_no for n in notices], ["GG-1", "GG-2", "GG-3"])
        # 中文日期可解析；人话文本保留原文并标记无法解析
        self.assertEqual(notices[1].start_date.isoformat(), "2026-11-13")
        self.assertIsNone(notices[2].start_date)
        self.assertEqual(notices[2].raw_start, "即日")
        # 原页地址按记录所在分页记录
        self.assertTrue(notices[0].page_url.endswith("/index.html"))
        self.assertTrue(notices[1].page_url.endswith("/page-2"))

    def test_broken_page_returns_partial_with_error(self):
        # 首页指向一个第 2 页会 500 的链路
        root = Path(self.tmp.name)
        (root / "chain.html").write_text(
            GOOD_PAGE_1.replace('href="/page-2"', 'href="/broken/page-2"'),
            encoding="utf-8",
        )
        notices, errors, failed_page, page_count = scrape_notices(self.url("/chain.html"))
        self.assertEqual(len(notices), 1)  # 只有失败前的部分数据
        self.assertTrue(errors)
        self.assertIn("抓取失败", errors[0])
        self.assertTrue(failed_page.endswith("/broken/page-2"))
        self.assertEqual(page_count, 2)

    def test_missing_columns_is_error(self):
        notices, errors, failed_page, page_count = scrape_notices(self.url("/bad.html"))
        self.assertEqual(notices, [])
        self.assertTrue(any("表头缺少必要列" in e for e in errors))
        self.assertTrue(failed_page.endswith("/bad.html"))

    def test_homepage_unreachable(self):
        notices, errors, failed_page, page_count = scrape_notices(
            f"http://127.0.0.1:{self.port}/no-such-page.html"
        )
        self.assertEqual(notices, [])
        self.assertTrue(errors)
        self.assertEqual(page_count, 1)


if __name__ == "__main__":
    unittest.main()
