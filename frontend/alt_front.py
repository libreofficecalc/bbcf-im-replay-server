import dash
from dash import dcc, html
from dash.dependencies import Input, Output, State
import pandas as pd
import mariadb
import sys


from characters import dropdown_pre_computed_label_value, character_keys

from credentials.DB_INFO_FRONT import DB_HOST, DB_USER, DB_PASSWORD, DATABASE


# Define the Dash app
app = dash.Dash(__name__)
app.title = "BBCF IM replay database"
front = app.server
WARNING_TEXT2 = "datetime_ is the local time where the replay was recorded. upload_datetime_ is the time in UTC-4 when the replay was uploaded."
WARNING_TEXT = "Showing latest 50 replays by upload time"
VIDEO_EXPLANATION_URL = "https://youtu.be/oVJ-JNeJBVo"
HREF_PREFIX = "http://50.118.225.175/uploads/"

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
                "IMPORTANT: Due to a catastrophic failure on the VPS provider all replays prior to 24/12/2024 are lost."
            )
        ),
        # html.Img(src = "assets/roundtable_de_bleis_banner.png"),
        # Date selection input
        dcc.DatePickerRange(
            id="date-range",
            display_format="YYYY-MM-DD",
            start_date_placeholder_text="yyyy-mm-dd",
            end_date_placeholder_text="yyyy-mm-dd",
            min_date_allowed="2020-01-01",
            max_date_allowed="2030-12-31",
        ),
        # Dropdown for p1_toon
        #        dcc.Input(id =  "p1-toon", type = 'number', placeholder= 'Enter toon'),
        html.Div(
            id="toon-select-div",
            children=dcc.Dropdown(
                id="p1-toon",
                options=dropdown_pre_computed_label_value,
                placeholder="Select character",
            ),
        ),
        # Text Input For P1
        dcc.Input(id="p1-input", type="text", placeholder="Enter player name"),
        # Text input for p1_steamid64
        dcc.Input(
            id="p1-steamid64-input", type="text", placeholder="Enter player steamid64"
        ),
        # Button to trigger the query
        html.Button("Query", id="query-button", n_clicks=0),
        html.Div(id="warning-text2", children=WARNING_TEXT2),
        html.Div(id="warning-text-latest", children=WARNING_TEXT),
        # Table to display query results
        html.Div(id="query-results"),
    ]
)


@app.callback(
    Output("query-results", "children"),
    Output("warning-text-latest", "children"),
    [Input("query-button", "n_clicks")],
    [State("date-range", "start_date"), State("date-range", "end_date")],
    [State("p1-input", "value"), State("p1-steamid64-input", "value")],
    State("p1-toon", "value"),
)
def update_query_results(n_clicks, start_date, end_date, p1, p1_steamid64, p1_toon):
    #        print(p1_toon)
    #        if n_clicks > 0:

    conn = mariadb.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DATABASE
    )

    cursor = conn.cursor(dictionary=True)
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
                                FROM replay_metadata"""
    params = ()
    order_clause = "ORDER BY upload_datetime_ desc, datetime_ desc"
    where_clause = f"WHERE TRUE "
    limit_clause = f"LIMIT 50" if n_clicks == 0 else ""

    if start_date is not None and end_date is not None:
        where_clause += f" AND datetime_ BETWEEN %s AND %s"
        params += (start_date, end_date)

    if p1 is not None:
        where_clause += f" AND (p1 LIKE '%{p1}%' OR p2 LIKE '%{p1}%' )"
        # params += (p1,p1)
    if p1_toon is not None:

        where_clause += f""" AND (p1_toon = %d  OR p2_toon = %d)"""
        params += (p1_toon, p1_toon)

    if p1_steamid64 is not None:
        where_clause += " AND (p1_steamid64 = %d OR p2_steamid64 = %d)"
        params += (p1_steamid64, p1_steamid64)

    query = f"""
            {base_query} 
            {where_clause}
            {order_clause}
            {limit_clause}
            

"""
    cursor.execute(query, params)
    result = cursor.fetchall()

    conn.close()
    df = pd.DataFrame(result)

    if len(df) == 0:
        return None, "No matches"
    #            print(df)
    df["p1_toon"] = df["p1_toon"].replace(character_keys)
    df["p2_toon"] = df["p2_toon"].replace(character_keys)

    # Create an HTML table to display the results
    style = {"border": "1px inset black"}
    style_outer = {"border": "1px outset black"}
    table_header = [html.Th(col) for col in df.columns]
    table_body = []
    for index, row in df.iterrows():

        table_row = []
        for col_name in df.columns:

            if col_name == "filename":
                href = HREF_PREFIX + row[col_name]
                table_row.append(html.Td(html.A(row[col_name], href=href)))
            else:
                table_row.append(html.Td(row[col_name]))

        table_body.append(html.Tr(table_row))

    table = html.Table([html.Thead(html.Tr(table_header)), html.Tbody(table_body)])
    warning_text = f"{len(df)} matches" if n_clicks > 0 else WARNING_TEXT
    return table, warning_text


if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=2000, debug=False)  # 2000
