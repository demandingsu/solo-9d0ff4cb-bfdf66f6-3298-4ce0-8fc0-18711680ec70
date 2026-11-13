"""停运公告抓取：从公告首页逐页解析公告表格。

约定的示例网站结构（也适用于同构的真实公告站点）：
  * 公告首页即分页第 1 页，含一张公告表格；
  * 表头出现 公告编号 / 车次号 / 生效起始日期 / 生效截止日期 等关键词；
  * 页脚有 ``a.next-page`` 指向下一页；最后一页没有该链接。

与时刻表抓取一样，任何一页抓取失败或表头缺列都会中断并返回错误，
调用方据此放弃本次部分结果、保留上一次完整批次。
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .notices import Notice, parse_notice_date

REQUEST_TIMEOUT = 10
MAX_PAGES = 50  # 防止异常站点造成无限翻页

_HEADER_ALIASES = {
    "notice_no": ("公告编号", "公告号", "编号", "文号", "notice", "no"),
    "trip_no": ("车次号", "车次", "班车", "班次", "trip", "bus"),
    "start_date": ("生效起始", "开始日期", "起始日期", "生效开始", "停运开始", "start"),
    "end_date": ("生效截止", "截止日期", "结束日期", "生效结束", "停运截止", "end"),
}


def _norm(text: str) -> str:
    return "".join((text or "").split()).lower()


def _header_index(headers: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    normed = [_norm(h) for h in headers]
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            key = _norm(alias)
            for i, header in enumerate(normed):
                if header and (header == key or key in header):
                    index[field_name] = i
                    break
            if field_name in index:
                break
    return index


def _fetch(session: requests.Session, url: str) -> str:
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def scrape_notices(
    home_url: str, session: requests.Session | None = None
) -> tuple[list[Notice], list[str], str | None, int]:
    """从公告首页开始逐页抓取，返回 ``(公告列表, 错误列表, 失败页 URL, 已访问页数)``。

    只要有一页抓取失败、缺表格或表头缺列，错误列表非空且 ``失败页 URL``
    指向出问题的分页；此时返回的公告列表只包含失败页之前的部分数据，
    调用方**不得**用它替换上一次完整批次。
    """
    session = session or requests.Session()
    notices: list[Notice] = []
    errors: list[str] = []
    seen_urls: set[str] = set()
    url: str | None = home_url
    page_no = 0
    broken = False
    failed_page_url: str | None = None

    while url and page_no < MAX_PAGES:
        page_no += 1
        if url in seen_urls:
            errors.append(f"公告分页出现循环链接（{url}），公告未取全。")
            broken = True
            failed_page_url = url
            break
        seen_urls.add(url)

        try:
            html = _fetch(session, url)
        except requests.RequestException as exc:
            errors.append(f"公告第 {page_no} 页抓取失败（{url}）：{exc}")
            broken = True
            failed_page_url = url
            break

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table is None:
            errors.append(f"公告第 {page_no} 页（{url}）找不到公告表格。")
            broken = True
            failed_page_url = url
            break

        header_row = table.find("tr")
        headers = [cell.get_text(strip=True) for cell in header_row.find_all(["th", "td"])]
        col = _header_index(headers)
        missing = [name for name in ("notice_no", "trip_no", "start_date", "end_date")
                   if name not in col]
        if missing:
            labels = {
                "notice_no": "公告编号",
                "trip_no": "车次号",
                "start_date": "生效起始日期",
                "end_date": "生效截止日期",
            }
            errors.append(
                f"公告第 {page_no} 页（{url}）表头缺少必要列："
                f"{'、'.join(labels[name] for name in missing)}。"
            )
            broken = True
            failed_page_url = url
            break

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(col.values()):
                continue
            notice_no = cells[col["notice_no"]].get_text(strip=True)
            trip_no = cells[col["trip_no"]].get_text(strip=True)
            if not notice_no or not trip_no:
                # 编号或车次缺失的行无法作为依据，跳过但不阻断整批抓取
                continue
            raw_start = cells[col["start_date"]].get_text(strip=True)
            raw_end = cells[col["end_date"]].get_text(strip=True)
            notices.append(
                Notice(
                    notice_no=notice_no,
                    trip_no=trip_no,
                    start_date=parse_notice_date(raw_start),
                    end_date=parse_notice_date(raw_end),
                    page_url=url,
                    raw_start=raw_start,
                    raw_end=raw_end,
                )
            )

        next_link = soup.select_one("a.next-page")
        url = urljoin(url, next_link["href"]) if next_link and next_link.get("href") else None

    if page_no >= MAX_PAGES and url:
        errors.append(f"公告分页超过 {MAX_PAGES} 页上限，公告可能未取全。")
        broken = True
        failed_page_url = url
    if broken:
        errors.append("公告分页未完整抓取，本次部分结果不予保存。")
    return notices, errors, failed_page_url, page_no
