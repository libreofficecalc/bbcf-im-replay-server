from dash import Dash, html, dcc, callback, Output, Input
from dash_dangerously_set_inner_html import DangerouslySetInnerHTML
import plotly.express as px
import pandas as pd
import subprocess
import sys
import re

def get_subprocess_stdout_run():
    stdout = subprocess.run(['sh', './test_front_query_script.sh'],text=True,  capture_output = True).stdout
    return stdout;
#    'margin-left': 'auto','margin-right': 'auto;'})

    
app = Dash('db dump front')
app.layout = html.Div([
        html.H1(children='replay_metadata db dump', style={'textAlign':'center'}),
        html.Div(id = 'table', children = []),
                 html.Div(id='trigger')
    ])

@callback(
        Output('table', 'children'),
        Input('trigger', 'style')
    )
def trigger_fetch_sh(style):
#    return html.Iframe(srcDoc = get_subprocess_stdout_run())
    table_html = get_subprocess_stdout_run()
    regex = r"<TD>([^<]*\.dat)<\/TD>"
    table_html = re.sub(regex, r'<TD><a href="http://50.118.225.175/uploads/\1">\1</TD>', table_html)
    return [DangerouslySetInnerHTML(table_html)]
    

if __name__ == '__main__':
    app.run(host = '0.0.0.0', port = 2000, debug=True)
