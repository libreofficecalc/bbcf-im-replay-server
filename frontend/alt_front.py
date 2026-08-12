import dash
from dash import dcc, html, callback_context, ALL
from dash.dependencies import Input, Output, State
from math import ceil
import json
import pandas as pd
import mariadb
import sys
import flask
import ipaddress


from characters import dropdown_pre_computed_label_value, character_keys

from credentials.DB_INFO_FRONT import DB_HOST, DB_USER, DB_PASSWORD, DATABASE


# Define the Dash app
app = dash.Dash(__name__)
app.title = "BBCF IM replay database"
front = app.server
WARNING_TEXT2 = "datetime_ is the local time where the replay was recorded. upload_datetime_ is the time in UTC-4 when the replay was uploaded."
WARNING_TEXT = "Showing latest 50 replays by upload time"
VIDEO_EXPLANATION_URL = "https://youtu.be/oVJ-JNeJBVo"
HREF_PREFIX_OPEN = "steam://run/586140/?load-replay="
DOWNLOAD_PORT = 5000  # backend download port, needed for direct ip access. the domain proxies /download so it doesnt show up there
PAGE_SIZE = 500

PAGINATION_STYLE = {
    "display": "flex", "gap": "4px", "alignItems": "center",
    "margin": "8px 0", "flexWrap": "wrap",
}

LINK_BTN = {
    "background": "none", "border": "none", "padding": "0 2px",
    "color": "#0000EE", "textDecoration": "underline",
    "cursor": "pointer", "font": "inherit",
}
CURRENT_BTN = {
    "background": "none", "border": "none", "padding": "0 2px",
    "fontWeight": "bold", "cursor": "default", "font": "inherit",
}


def build_pagination(current_page, total_pages, loc):
    """Pagination row with 'Page X of Y' label and numbered link-buttons."""
    if total_pages <= 1:
        return html.Div(style={"display": "none"})

    # Which page indices to show (windowed)
    to_show = set()
    to_show.update([0, 1])
    to_show.update([total_pages - 2, total_pages - 1])
    to_show.update([current_page - 1, current_page, current_page + 1])
    to_show = sorted(p for p in to_show if 0 <= p < total_pages)

    children = [
        html.Span(
            f"Page {current_page + 1} of {total_pages}",
            style={"marginRight": "8px"},
        )
    ]
    prev = -1
    for p in to_show:
        if p > prev + 1:
            children.append(html.Span("…", style={"padding": "0 6px"}))
        if p == current_page:
            children.append(
                html.Button(
                    str(p + 1),
                    id={"type": f"page-btn-{loc}", "index": p},
                    n_clicks=0,
                    disabled=True,
                    style=CURRENT_BTN,
                )
            )
        else:
            children.append(
                html.Button(
                    str(p + 1),
                    id={"type": f"page-btn-{loc}", "index": p},
                    n_clicks=0,
                    style=LINK_BTN,
                )
            )
        prev = p

    return html.Div(children, style=PAGINATION_STYLE)


# Define the layout of the app
app.layout = html.Div(
    [
        html.H1("replay DB"),
        html.Div(
            html.A(
                "HOW TO DOWNLOAD AND PLAY REPLAYS",
                href=VIDEO_EXPLANATION_URL,
                target="_blank",
            )
        ),
        html.Div(
            html.H3(
                "IMPORTANT: Replays from 2026-02-09 to 2026-05-23 are gone due to the previous VPS provider (Hostslick) abruptly stopping services and not responding to tickets, forcing us to change providers."
            )
        ),
        # html.Img(src = "assets/roundtable_de_bleis_banner.png"),
        html.Div(
            [
                dcc.DatePickerRange(
                    id="date-range",
                    display_format="YYYY-MM-DD",
                    start_date_placeholder_text="yyyy-mm-dd",
                    end_date_placeholder_text="yyyy-mm-dd",
                    min_date_allowed="2020-01-01",
                    max_date_allowed="2030-12-31",
                ),
                html.Div(
                    id="toon-select-div",
                    children=dcc.Dropdown(
                        id="p1-toon",
                        options=dropdown_pre_computed_label_value,
                        placeholder="Select character",
                    ),
                    style={"width": "180px"},
                ),
                dcc.Input(id="p1-input", type="text", placeholder="Enter player name",
                          style={"width": "15%"}),
                dcc.Input(
                    id="p1-steamid64-input", type="text", placeholder="Enter player steamid64",
                    style={"width": "15%"}
                ),
                html.Button("Query", id="query-button", n_clicks=0),
            ],
            style={"display": "flex", "alignItems": "center", "gap": "8px", "flexWrap": "wrap"},
        ),
        html.Div(id="warning-text2", children=WARNING_TEXT2),
        html.Div(id="warning-text-latest", children=WARNING_TEXT),
        dcc.Store(id="page-num", data=0),
        dcc.Store(id="page-request", data={"page": 0}),
        # Table + pagination (top and bottom) all rendered together
        html.Div(id="query-results"),
    ]
)


@app.callback(
    Output("page-request", "data"),
    Input("query-button", "n_clicks"),
    Input({"type": "page-btn-top", "index": ALL}, "n_clicks"),
    Input({"type": "page-btn-bottom", "index": ALL}, "n_clicks"),
    State("page-num", "data"),
    prevent_initial_call=True,
)
def handle_navigation(query_clicks, top_clicks, bottom_clicks, current_page):
    """Translate any button click into a page-request."""
    triggered = callback_context.triggered[0]["prop_id"]
    if "query-button" in triggered:
        return {"page": 0}
    if "page-btn-top" in triggered or "page-btn-bottom" in triggered:
        idx = json.loads(triggered.split(".")[0])["index"]
        return {"page": idx}
    raise dash.exceptions.PreventUpdate


@app.callback(
    Output("query-results", "children"),
    Output("warning-text-latest", "children"),
    Output("page-num", "data"),
    Input("page-request", "data"),
    State("query-button", "n_clicks"),
    State("date-range", "start_date"),
    State("date-range", "end_date"),
    State("p1-input", "value"),
    State("p1-steamid64-input", "value"),
    State("p1-toon", "value"),
)
def fetch_data(page_request, query_clicks, start_date, end_date, p1, p1_steamid64, p1_toon):
    page_num = page_request.get("page", 0)

    conn = mariadb.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DATABASE
    )
    cursor = conn.cursor(dictionary=True)

    params = ()
    where_clause = "WHERE TRUE "

    has_date_filter = start_date is not None and end_date is not None
    has_like_filter = p1 is not None

    if has_date_filter:
        # Filter on both columns: lets idx_sort (upload_datetime_, datetime_) do a range scan
        # and cover the ORDER BY with early stop at LIMIT PAGE_SIZE.
        # Valid because upload_datetime_ = datetime_ for all stored replays.
        where_clause += " AND datetime_ BETWEEN %s AND %s AND upload_datetime_ BETWEEN %s AND %s"
        params += (start_date, end_date, start_date, end_date)

    if has_like_filter:
        where_clause += f" AND (p1 LIKE '%{p1}%' OR p2 LIKE '%{p1}%' )"

    if p1_toon is not None:
        where_clause += " AND (p1_toon = %d OR p2_toon = %d)"
        params += (p1_toon, p1_toon)

    if p1_steamid64 is not None:
        try:
            steamid64_int = int(p1_steamid64)
            where_clause += " AND (p1_steamid64 = %s OR p2_steamid64 = %s)"
            params += (steamid64_int, steamid64_int)
        except (ValueError, TypeError):
            pass  # non-numeric input, skip this filter

    order_clause = "ORDER BY upload_datetime_ DESC, datetime_ DESC"

    # For LIKE without a date range: ignore idx_sort to avoid 1M random-I/O lookups
    # (sequential table scan + filesort is faster for wildcard searches)
    ignore_hint = "IGNORE INDEX (idx_sort)" if (has_like_filter and not has_date_filter) else ""

    has_any_filter = has_date_filter or has_like_filter or p1_toon is not None or p1_steamid64 is not None

    if not has_any_filter:
        # No filters active — always show latest 50 regardless of query_clicks
        limit_clause = "LIMIT 50"
    else:
        limit_clause = f"LIMIT {PAGE_SIZE} OFFSET {page_num * PAGE_SIZE}"

    base_query = f"""SELECT
                                datetime_,
                                p1,
                                p1_toon,
                                p2,
                                p2_toon,
                                recorder,
                                winner,
                                filename,
                                CAST(p1_steamid64 as char(50)) as p1_steamid64,
                                CAST(p2_steamid64 as char(50)) as p2_steamid64,
                                CAST(recorder_steamid64 as char(50)) as recorder_steamid64,
                                upload_datetime_
                                FROM replay_metadata {ignore_hint}"""

    query = f"{base_query} {where_clause} {order_clause} {limit_clause}"
    cursor.execute(query, params)
    result = cursor.fetchall()

    # Determine total count / pages (only for filtered queries)
    total_count = None
    total_pages = None
    if has_any_filter:
        if len(result) == 0 and page_num > 0:
            # Overshot the last page — find the real last page and re-query
            count_cursor = conn.cursor()
            count_cursor.execute(
                f"SELECT COUNT(*) FROM replay_metadata {ignore_hint} {where_clause}", params
            )
            total_count = count_cursor.fetchone()[0]
            if total_count > 0:
                total_pages = ceil(total_count / PAGE_SIZE)
                page_num = total_pages - 1
                cursor.execute(
                    f"{base_query} {where_clause} {order_clause} "
                    f"LIMIT {PAGE_SIZE} OFFSET {page_num * PAGE_SIZE}",
                    params,
                )
                result = cursor.fetchall()
        elif len(result) == PAGE_SIZE:
            # Page is full — run COUNT(*) to know how many pages there are
            count_cursor = conn.cursor()
            count_cursor.execute(
                f"SELECT COUNT(*) FROM replay_metadata {ignore_hint} {where_clause}", params
            )
            total_count = count_cursor.fetchone()[0]
            total_pages = ceil(total_count / PAGE_SIZE)
        else:
            # Current page is the last one
            total_count = page_num * PAGE_SIZE + len(result)
            total_pages = page_num + 1

    conn.close()
    df = pd.DataFrame(result)

    if len(df) == 0:
        return None, "No matches", 0

    df["p1_toon"] = df["p1_toon"].replace(character_keys)
    df["p2_toon"] = df["p2_toon"].replace(character_keys)
    df["open"] = df["filename"].copy()
    df = df[["upload_datetime_",
             "p1", "p1_toon", "p2", "p2_toon",
             "recorder", "winner", "open", "filename",
             "p1_steamid64", "p2_steamid64", "recorder_steamid64",
             "datetime_"]]

    # build the links off however they reached the page instead of hardcoding it.
    # raw ip = they hit the box directly so downloads have to point at the backend
    # port (:5000). a domain = they came through cloudflare which proxies /download
    # on the standard port, so no port there.
    host = flask.request.host
    hostname = host.split(":")[0]
    try:
        ipaddress.ip_address(hostname)
        is_ip = True
    except ValueError:
        is_ip = False

    if is_ip:
        prefix = f"http://{hostname}:{DOWNLOAD_PORT}/download/"
        download_prefix = prefix
        open_download_prefix = prefix
    else:
        # X-Forwarded-Proto for the real scheme cause the origin only sees http,
        # otherwise chrome blocks the http download on an https page
        scheme = flask.request.headers.get("X-Forwarded-Proto", flask.request.scheme)
        download_prefix = f"{scheme}://{host}/download/"
        # open link stays http, the game grabs it itself so mixed content doesnt matter
        open_download_prefix = f"http://{host}/download/"

    table_header = [html.Th(col) for col in df.columns]
    table_body = []
    for _, row in df.iterrows():
        table_row = []
        for col_name in df.columns:
            if col_name == "filename":
                href = download_prefix + row[col_name]
                table_row.append(html.Td(html.A(row[col_name], href=href)))
            elif col_name == "open":
                href = HREF_PREFIX_OPEN + open_download_prefix + row[col_name]
                table_row.append(html.Td(html.A("open", href=href)))
            else:
                table_row.append(html.Td(row[col_name]))
        table_body.append(html.Tr(table_row))

    table = html.Table([html.Thead(html.Tr(table_header)), html.Tbody(table_body)])

    if not has_any_filter:
        return table, WARNING_TEXT, 0

    warning_text = f"{total_count} matches"
    content = [
        build_pagination(page_num, total_pages, "top"),
        table,
        build_pagination(page_num, total_pages, "bottom"),
    ]

    return content, warning_text, page_num


if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=2000, debug=False)  # 2000
