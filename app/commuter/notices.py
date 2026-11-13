"""停运公告的纯逻辑：生效日期解析与服务日命中判定。

不依赖网络与数据库，便于单元测试。抓取由 :mod:`commuter.notice_scraper`
负责，批次存储由 :mod:`commuter.notice_store` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

# 常见中文/数字日期写法：2026-11-13、2026/11/13、2026年11月13日、20261113
_DATE_PATTERNS = (
    ("%Y-%m-%d", re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")),
    ("%Y/%m/%d", re.compile(r"^\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*$")),
    ("%Y.%m.%d", re.compile(r"^\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*$")),
    ("%Y年%m月%d日", re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*$")),
    ("%Y%m%d", re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")),
)


@dataclass(frozen=True)
class Notice:
    """一条停运公告记录（从分页表格解析得到）。"""

    notice_no: str       # 公告编号
    trip_no: str         # 车次号
    start_date: date | None  # 生效起始日期（无法解析时为 None）
    end_date: date | None    # 生效截止日期（无法解析时为 None）
    page_url: str        # 该记录所在分页的原始地址
    raw_start: str = ""  # 起始日期原始文本，便于页面核对
    raw_end: str = ""    # 截止日期原始文本，便于页面核对

    def effective_on(self, day: date) -> bool:
        """判断该公告是否在给定服务日生效。

        两端都可解析时按闭区间判断；只有一端可解析时按单侧开区间判断；
        两端都无法解析时保守视为“无法确认”，返回 ``False`` 并由调用方告警。
        """
        if self.start_date is None and self.end_date is None:
            return False
        if self.start_date is not None and day < self.start_date:
            return False
        if self.end_date is not None and day > self.end_date:
            return False
        return True

    def has_unparseable_dates(self) -> bool:
        """是否存在应当有值却无法解析的日期文本。"""
        return (bool(self.raw_start) and self.start_date is None) or (
            bool(self.raw_end) and self.end_date is None
        )


def parse_notice_date(value: str) -> date | None:
    """把公告页上的日期文本解析为 :class:`date`，无法解析返回 ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for _fmt, pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def notices_effective_on(notices: list[Notice], day: date) -> tuple[list[Notice], list[Notice]]:
    """按服务日筛选公告，返回 ``(当日生效列表, 日期无法确认的列表)``。

    日期文本缺失或无法解析的公告不能参与“当日无停运”的结论，
    单独返回由界面提示人工核对原页。
    """
    effective: list[Notice] = []
    unconfirmed: list[Notice] = []
    for notice in notices:
        if notice.has_unparseable_dates():
            unconfirmed.append(notice)
        elif notice.effective_on(day):
            effective.append(notice)
    effective.sort(key=lambda item: (item.trip_no, item.notice_no))
    return effective, unconfirmed
