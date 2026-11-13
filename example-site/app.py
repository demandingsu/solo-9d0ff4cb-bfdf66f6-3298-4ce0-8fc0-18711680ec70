"""本地示例时刻网站（演示用，非生产代码）。

特点：
  * 首页列出全部站点链接；
  * 每个站点一页表格，按页大小分页，页脚带“下一页”；
  * 含 24:xx / 25:xx 跨日时刻、工作日 / 周末 / 每日服务日；
  * N206 故意缺少中央厂区到站时刻，演示“缺少关键时刻”提示；
  * “故障北站”第 2 页返回 500，演示分页抓取失败提示；
  * 提供两套停运公告板：
      - /notices/        跨多页的正常公告（含一条日期无法解析的公告）；
      - /notices-broken/ 第 2 页返回 500，演示公告抓取失败时保留旧批次。
"""

from __future__ import annotations

from flask import Flask, abort, render_template_string

app = Flask(__name__)
PAGE_SIZE = 4
NOTICE_PAGE_SIZE = 3

# (车次号, 停站顺序, 站点, 时刻, 服务日)
TRIPS: list[tuple[str, int, str, str, str]] = [
    ("N201", 1, "滨河园区", "22:30", "工作日"),
    ("N201", 2, "东门枢纽", "22:55", "工作日"),
    ("N201", 3, "中央厂区", "23:40", "工作日"),
    ("N201", 4, "夜班宿舍", "00:10", "工作日"),

    ("N202", 1, "滨河园区", "23:15", "每日"),
    ("N202", 2, "东门枢纽", "23:40", "每日"),
    ("N202", 3, "中央厂区", "24:25", "每日"),
    ("N202", 4, "夜班宿舍", "24:55", "每日"),

    ("N203", 1, "滨河园区", "24:00", "工作日"),
    ("N203", 2, "东门枢纽", "24:25", "工作日"),
    ("N203", 3, "中央厂区", "25:10", "工作日"),
    ("N203", 4, "夜班宿舍", "25:40", "工作日"),

    ("N204", 1, "滨河园区", "23:50", "周末"),
    ("N204", 2, "东门枢纽", "00:15", "周末"),
    ("N204", 3, "中央厂区", "01:00", "周末"),
    ("N204", 4, "夜班宿舍", "01:30", "周末"),

    ("N205", 1, "滨河园区", "24:40", "每日"),
    ("N205", 2, "东门枢纽", "25:05", "每日"),
    ("N205", 3, "中央厂区", "25:50", "每日"),
    ("N205", 4, "夜班宿舍", "26:20", "每日"),

    # 故意缺少中央厂区到站时刻：只能在其他站看到该车次
    ("N206", 1, "滨河园区", "25:30", "每日"),
    ("N206", 2, "东门枢纽", "25:55", "每日"),
    ("N206", 4, "夜班宿舍", "27:10", "每日"),

    # 故障北站的记录：访问其第 2 页时返回 500
    ("X901", 1, "故障北站", "22:10", "每日"),
    ("X901", 2, "中央厂区", "23:00", "每日"),
    ("X902", 1, "故障北站", "23:20", "每日"),
    ("X902", 2, "中央厂区", "00:05", "每日"),
    ("X903", 1, "故障北站", "24:30", "每日"),
    ("X903", 2, "中央厂区", "25:15", "每日"),
    ("X904", 1, "故障北站", "25:40", "每日"),
    ("X904", 2, "中央厂区", "26:25", "每日"),
    ("X905", 1, "故障北站", "26:50", "每日"),
    ("X905", 2, "中央厂区", "27:35", "每日"),
]

STATIONS = sorted({row[2] for row in TRIPS})

# (公告编号, 车次号, 生效起始日期, 生效截止日期)
# 覆盖多个服务日，方便演示按日筛选；GG-2026-0006 的日期故意写成人话文本，
# 演示“日期无法解析、需人工核对”的保守处理。
NOTICES: list[tuple[str, str, str, str]] = [
    ("GG-2026-0001", "N201", "2026-11-10", "2026-11-20"),
    ("GG-2026-0002", "N203", "2026-11-13", "2026-11-13"),
    ("GG-2026-0003", "N204", "2026-11-14", "2026-11-15"),
    ("GG-2026-0004", "X902", "2026-11-01", "2026-12-31"),
    ("GG-2026-0005", "N205", "2026-11-16", "2026-11-30"),
    ("GG-2026-0006", "N202", "即日", "另行通知"),
    ("GG-2026-0007", "X905", "2026-11-12", "2026-11-18"),
]

HOME_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>示例夜班接驳时刻表</title>
<style>body{font-family:sans-serif;margin:32px;} li{margin:6px;}</style></head>
<body><h1>示例夜班接驳时刻表（首页）</h1>
<p>点击站点进入该站分页时刻表：</p>
<ul>
{% for s in stations %}
  <li><a class="station-link" href="{{ url_for('station', station=s, page=1) }}">{{ s }}</a></li>
{% endfor %}
</ul>
<h2>停运公告</h2>
<ul>
  <li><a href="{{ url_for('notices', page=1) }}">临时停运公告（跨页正常数据）</a></li>
  <li><a href="{{ url_for('notices_broken', page=1) }}">临时停运公告（故障数据源，第 2 页 500）</a></li>
</ul>
<p class="muted">提示：「故障北站」第 2 页会返回 HTTP 500，用于演示抓取失败提示。</p>
</body></html>
"""

NOTICE_PAGE_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>临时停运公告 第 {{ page }} 页</title>
<style>body{font-family:sans-serif;margin:32px;}
table{border-collapse:collapse;} th,td{border:1px solid #888;padding:6px 12px;}
.nav{margin-top:14px;}</style></head>
<body>
<h1>临时停运公告（第 {{ page }} / {{ total_pages }} 页）</h1>
<table>
  <tr><th>公告编号</th><th>车次号</th><th>生效起始日期</th><th>生效截止日期</th></tr>
  {% for row in rows %}
  <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
  {% endfor %}
</table>
<div class="nav">
  {% if page > 1 %}<a href="{{ prev_endpoint(page-1) }}">上一页</a>{% endif %}
  {% if page < total_pages %}
    <a class="next-page" href="{{ prev_endpoint(page+1) }}">下一页 →</a>
  {% endif %}
  <p><a href="{{ url_for('home') }}">返回首页</a></p>
</div>
</body></html>
"""

PAGE_HTML = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{{ station }} 时刻表 第 {{ page }} 页</title>
<style>body{font-family:sans-serif;margin:32px;}
table{border-collapse:collapse;} th,td{border:1px solid #888;padding:6px 12px;}
.nav{margin-top:14px;}</style></head>
<body>
<h1>{{ station }} · 停站时刻表（第 {{ page }} / {{ total_pages }} 页）</h1>
<table>
  <tr><th>车次号</th><th>停站顺序</th><th>站点</th><th>时刻</th><th>服务日</th></tr>
  {% for row in rows %}
  <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
  {% endfor %}
</table>
<div class="nav">
  {% if page > 1 %}<a href="{{ url_for('station', station=station, page=page-1) }}">上一页</a>{% endif %}
  {% if page < total_pages %}
    <a class="next-page" href="{{ url_for('station', station=station, page=page+1) }}">下一页 →</a>
  {% endif %}
  <p><a href="{{ url_for('home') }}">返回首页</a></p>
</div>
</body></html>
"""


@app.route("/")
def home():
    return render_template_string(HOME_HTML, stations=STATIONS)


@app.route("/station/<station>/page-<int:page>")
def station(station: str, page: int):
    if station not in STATIONS:
        abort(404)
    if station == "故障北站" and page >= 2:
        abort(500, description="演示用：该站点分页服务器临时故障")

    rows = [r for r in TRIPS if r[2] == station]
    total_pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 1 or page > total_pages:
        abort(404)
    chunk = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return render_template_string(
        PAGE_HTML, station=station, page=page, total_pages=total_pages, rows=chunk
    )


def _render_notice_page(page: int, broken: bool):
    if broken and page >= 2:
        abort(500, description="演示用：公告分页服务器临时故障")
    total_pages = (len(NOTICES) + NOTICE_PAGE_SIZE - 1) // NOTICE_PAGE_SIZE
    if page < 1 or page > total_pages:
        abort(404)
    chunk = NOTICES[(page - 1) * NOTICE_PAGE_SIZE: page * NOTICE_PAGE_SIZE]

    def prev_endpoint(target_page: int) -> str:
        return f"/notices-broken/page-{target_page}" if broken else f"/notices/page-{target_page}"

    return render_template_string(
        NOTICE_PAGE_HTML,
        page=page,
        total_pages=total_pages,
        rows=chunk,
        prev_endpoint=prev_endpoint,
    )


@app.route("/notices/")
@app.route("/notices/page-<int:page>")
def notices(page: int = 1):
    return _render_notice_page(page, broken=False)


@app.route("/notices-broken/")
@app.route("/notices-broken/page-<int:page>")
def notices_broken(page: int = 1):
    return _render_notice_page(page, broken=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
