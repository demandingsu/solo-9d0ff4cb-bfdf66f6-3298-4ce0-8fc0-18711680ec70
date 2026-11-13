"""停运公告查询路由：抓取公告、按来源落库批次、按服务日列出停运车次。"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime

import requests
from flask import Blueprint, current_app, render_template, request

from .notice_scraper import scrape_notices
from .notice_store import (
    connect,
    get_latest_complete,
    get_latest_failure,
    save_complete_batch,
    save_failed_batch,
)
from .notices import notices_effective_on

bp = Blueprint("notices", __name__, url_prefix="/notices")


def _db_path() -> str:
    return current_app.config["NOTICE_DB_PATH"]


def _parse_service_day(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@bp.route("/", methods=["GET", "POST"])
def query():
    form = {"home_url": "", "service_date": ""}
    errors: list[str] = []
    warnings: list[str] = []

    # 展示用状态
    batch = None                 # 依据的完整批次（dict 行）
    all_notices: list = []       # 该完整批次的全部公告明细
    effective = []               # 当日生效的公告
    unconfirmed = []             # 日期无法解析、需人工核对的公告
    failure = None               # 最近一次失败批次
    fresh_success = False        # 本次 POST 是否刚完成一次完整抓取
    service_day: date | None = None

    if request.method == "POST":
        form.update({key: request.form.get(key, "").strip() for key in form})
        if not form["home_url"]:
            errors.append("请填写公告首页 URL。")
        service_day = _parse_service_day(form["service_date"])
        if service_day is None:
            errors.append("服务日期格式无效，请通过日期选择器填写。")

        if not errors:
            try:
                notices, scrape_errors, failed_page, page_count = scrape_notices(
                    form["home_url"], requests.Session()
                )
            except requests.RequestException as exc:
                # 首页本身就打不开：按失败批次记录，不产生任何公告明细
                notices, scrape_errors, failed_page, page_count = (
                    [],
                    [f"公告首页抓取失败（{form['home_url']}）：{exc}"],
                    form["home_url"],
                    0,
                )

            with closing(connect(_db_path())) as conn:
                if scrape_errors:
                    save_failed_batch(
                        conn,
                        form["home_url"],
                        errors=scrape_errors,
                        failed_page_url=failed_page,
                    )
                else:
                    save_complete_batch(
                        conn,
                        form["home_url"],
                        notices,
                        page_count=page_count,
                    )
                    fresh_success = True
                batch, all_notices = get_latest_complete(conn, form["home_url"])
                failure = get_latest_failure(conn, form["home_url"])

            if scrape_errors:
                errors.extend(scrape_errors)
            if batch is not None:
                effective, unconfirmed = notices_effective_on(all_notices, service_day)
                if unconfirmed:
                    warnings.append(
                        f"{len(unconfirmed)} 条公告的生效日期无法解析，未纳入停运判断，"
                        "请按原页地址人工核对。"
                    )
            elif not scrape_errors:
                # 完整抓取成功但批次为 None 的情况理论上不会发生，留作防御
                errors.append("公告批次保存失败，无法判断当日停运情况。")
    else:
        # GET 时允许带上来源 URL 直接回看已存批次
        home_url = request.args.get("home_url", "").strip()
        if home_url:
            form["home_url"] = home_url
            with closing(connect(_db_path())) as conn:
                batch, all_notices = get_latest_complete(conn, home_url)
                failure = get_latest_failure(conn, home_url)
            if batch is not None:
                day_text = request.args.get("service_date", "").strip()
                service_day = _parse_service_day(day_text)
                form["service_date"] = day_text
                if service_day is not None:
                    effective, unconfirmed = notices_effective_on(all_notices, service_day)
                    if unconfirmed:
                        warnings.append(
                            f"{len(unconfirmed)} 条公告的生效日期无法解析，未纳入停运判断，"
                            "请按原页地址人工核对。"
                        )

    return render_template(
        "notices.html",
        form=form,
        errors=errors,
        warnings=warnings,
        batch=batch,
        all_notices=all_notices,
        failure=failure,
        effective=effective,
        unconfirmed=unconfirmed,
        fresh_success=fresh_success,
        service_day=service_day,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
