"""停运公告纯逻辑单元测试：日期解析、生效区间、按服务日筛选。"""

import unittest
from datetime import date

from commuter.notices import Notice, notices_effective_on, parse_notice_date


def make_notice(no="GG-1", trip="N201", start="2026-11-10", end="2026-11-20",
                url="http://x/notices/page-1"):
    return Notice(
        notice_no=no,
        trip_no=trip,
        start_date=parse_notice_date(start) if start else None,
        end_date=parse_notice_date(end) if end else None,
        page_url=url,
        raw_start=start or "",
        raw_end=end or "",
    )


class ParseDateTests(unittest.TestCase):
    def test_various_formats(self):
        self.assertEqual(parse_notice_date("2026-11-13"), date(2026, 11, 13))
        self.assertEqual(parse_notice_date("2026/11/13"), date(2026, 11, 13))
        self.assertEqual(parse_notice_date("2026.11.13"), date(2026, 11, 13))
        self.assertEqual(parse_notice_date("2026年11月13日"), date(2026, 11, 13))
        self.assertEqual(parse_notice_date("20261113"), date(2026, 11, 13))

    def test_bad_dates(self):
        self.assertIsNone(parse_notice_date(""))
        self.assertIsNone(parse_notice_date("即日"))
        self.assertIsNone(parse_notice_date("另行通知"))
        self.assertIsNone(parse_notice_date("2026-13-40"))
        self.assertIsNone(None) if False else self.assertIsNone(parse_notice_date(None))


class EffectiveOnTests(unittest.TestCase):
    def test_closed_range(self):
        notice = make_notice(start="2026-11-10", end="2026-11-20")
        self.assertTrue(notice.effective_on(date(2026, 11, 10)))
        self.assertTrue(notice.effective_on(date(2026, 11, 15)))
        self.assertTrue(notice.effective_on(date(2026, 11, 20)))
        self.assertFalse(notice.effective_on(date(2026, 11, 9)))
        self.assertFalse(notice.effective_on(date(2026, 11, 21)))

    def test_single_day(self):
        notice = make_notice(start="2026-11-13", end="2026-11-13")
        self.assertTrue(notice.effective_on(date(2026, 11, 13)))
        self.assertFalse(notice.effective_on(date(2026, 11, 12)))

    def test_one_sided_range(self):
        only_start = make_notice(start="2026-11-10", end="")
        self.assertTrue(only_start.effective_on(date(2026, 12, 1)))
        self.assertFalse(only_start.effective_on(date(2026, 11, 9)))
        only_end = make_notice(start="", end="2026-11-20")
        self.assertTrue(only_end.effective_on(date(2026, 1, 1)))
        self.assertFalse(only_end.effective_on(date(2026, 11, 21)))

    def test_both_unparseable_never_effective(self):
        notice = make_notice(start="即日", end="另行通知")
        self.assertTrue(notice.has_unparseable_dates())
        self.assertFalse(notice.effective_on(date(2026, 11, 13)))


class FilterTests(unittest.TestCase):
    def test_effective_and_unconfirmed_split(self):
        in_range = make_notice(no="GG-1", trip="N201", start="2026-11-10", end="2026-11-20")
        out_range = make_notice(no="GG-2", trip="N203", start="2026-12-01", end="2026-12-02")
        unparsed = make_notice(no="GG-3", trip="N202", start="即日", end="另行通知")
        effective, unconfirmed = notices_effective_on(
            [in_range, out_range, unparsed], date(2026, 11, 13)
        )
        self.assertEqual([n.notice_no for n in effective], ["GG-1"])
        self.assertEqual([n.notice_no for n in unconfirmed], ["GG-3"])

    def test_empty_input(self):
        effective, unconfirmed = notices_effective_on([], date(2026, 11, 13))
        self.assertEqual(effective, [])
        self.assertEqual(unconfirmed, [])


if __name__ == "__main__":
    unittest.main()
