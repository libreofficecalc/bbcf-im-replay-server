"""Replay database API, version 2: every filter the replay browsers and bot makers need, fast.

Added beside the existing endpoints (/upload, /get_replays, /download stay as they are).
Registered in app.py with app.register_blueprint(api_v2).

Endpoints (GET, JSON unless noted):
  /api/v2/replays            replays matching any combination of filters, newest first, by pages
  /api/v2/players            players by name (every name a Steam account has used)
  /api/v2/players/<id>       one player: names, characters played, wins, levels, last played
  /api/v2/players/<id>/replays?character=<c>
                             every replay of a player on a character in one answer (compact rows)
  /api/v2/characters/<toon>/players
                             who plays a character: by replays, by level, or by last played
  /api/v2/filenames          every filename after a given one (for mirroring the files)
  /api/v2/bundle             up to 100 replay files by GET (files=a,b), 1000 by POST: one tar of the stored .gz files

Statistics (player pages, leaderboards, the overview) are in stats.py.

Why it is fast (see docs/API_V2.md):
  - Pages continue from the last row seen (a cursor) instead of OFFSET, so page 300 costs what
    page 1 costs.
  - "This player" and "either side" queries run as two index range reads, one per side, merged:
    no scan of the table.
  - Name searches look in the players table (one row per account and name, ~100k rows), never
    with LIKE '%name%' over 1.5M replays.
  - Counts come from the per-player summaries where they can; a COUNT(*) over the replays only
    when asked for (count=1) and capped.

Every value reaches SQL as a parameter (no string building with user input).
"""
from datetime import datetime
from flask import Blueprint, request, jsonify, abort, Response, current_app, g
import gzip, io, os, re, tarfile
import mariadb

from credentials.config import Config

api_v2 = Blueprint("api_v2", __name__)

MAX_LIMIT = 500
COUNT_CAP = 10000   # "10,000+": a count never reads more than this
COLUMNS = ("filename, datetime_, upload_datetime_, p1, p1_toon, p2, p2_toon, recorder, winner, "
           "p1_steamid64, p2_steamid64, recorder_steamid64, p1_level, p2_level")


# No API query may run longer than this: a search too broad for the indexes is refused (503)
# instead of keeping the database busy.
MAX_SECONDS = 5


def connect():
    """One connection per request, closed when the request ends whatever happens (teardown
    below). Autocommit: a read never leaves a transaction open, so nothing holds the table's
    metadata lock between requests (an open one blocks schema changes, and every query after
    them)."""
    conn = mariadb.connect(host=Config.DB_HOST, port=getattr(Config, "DB_PORT", 3306), user=Config.DB_USER,
                           password=Config.DB_PASSWORD, database=Config.DATABASE, autocommit=True)
    conn.cursor().execute(f"SET SESSION max_statement_time = {MAX_SECONDS}")
    g.v2_connections = getattr(g, "v2_connections", []) + [conn]
    return conn


@api_v2.teardown_app_request
def close_connections(_error):
    for conn in getattr(g, "v2_connections", []):
        try:
            conn.close()
        except mariadb.Error:
            pass


@api_v2.errorhandler(mariadb.Error)
def database_error(e):
    if getattr(e, "errno", None) == 1969:   # max_statement_time exceeded
        return jsonify({"error": "this search takes too long: add a player or a character"}), 503
    return jsonify({"error": "database error"}), 500


def arg_int(name, lo=None, hi=None):
    v = request.args.get(name)
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except ValueError:
        abort(400, f"{name} must be a number")
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        abort(400, f"{name} out of range")
    return n


def arg_date(name):
    v = request.args.get(name)
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    abort(400, f"{name} must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")


def arg_choice(name, choices, default):
    v = request.args.get(name, default)
    if v not in choices:
        abort(400, f"{name} must be one of: {', '.join(choices)}")
    return v


def row_out(r):
    d = dict(r)
    for k in ("datetime_", "upload_datetime_"):
        if d.get(k) is not None:
            d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
    for k in ("p1_steamid64", "p2_steamid64", "recorder_steamid64"):
        if d.get(k) is not None:
            d[k] = str(d[k])   # 64-bit ids lose digits as JSON numbers in JavaScript
    return d


def encode_cursor(row, sort_col):
    return f"{row[sort_col].strftime('%Y%m%d%H%M%S')}.{row['filename']}"


def decode_cursor(c):
    try:
        when, name = c.split(".", 1)
        return datetime.strptime(when, "%Y%m%d%H%M%S"), name
    except ValueError:
        abort(400, "bad cursor")


@api_v2.route("/api/v2/replays")
def replays():
    """Filters (all optional, combined with AND):
      player=<steamid64>          one person's replays (either side unless side=1/2)
      side=any|1|2                which side `player` played (default any)
      character=<0-35>            the character `player` used; without `player`, either side
      opponent=<steamid64>        the other person
      opponent_name=<text>        the other person by name (any name the account used)
      opponent_character=<0-35>   the other side's character
      p1_character, p2_character  characters by side (exact sides)
      name=<text>                 a player whose name contains this (any name the account used)
      result=win|loss             the searched side won / lost (needs a player, name or character)
      winner=1|2                  side that won
      min_level, max_level        the searched side's level (with no player, name or character: both sides)
      opponent_min_level, opponent_max_level   the other side's level
      from, to                    match date (local time of the recorder)
      uploaded_from, uploaded_to  upload date
      recorder=<steamid64>        who uploaded it
    Order: sort=newest|oldest (match date) or uploaded (newest upload first). Pages: limit (max
    500), then cursor=<next_cursor of the previous page>. count=1 adds the number of matches
    (up to 10000, then count_capped)."""
    player = arg_int("player", 0)
    side = arg_choice("side", ("any", "1", "2"), "any")
    character = arg_int("character", 0, 99)
    opponent = arg_int("opponent", 0)
    opp_char = arg_int("opponent_character", 0, 99)
    p1c, p2c = arg_int("p1_character", 0, 99), arg_int("p2_character", 0, 99)
    name = (request.args.get("name") or "").strip()
    opp_name = (request.args.get("opponent_name") or "").strip()
    result = arg_choice("result", ("any", "win", "loss"), "any")
    winner = arg_int("winner", 1, 2)
    min_level, max_level = arg_int("min_level", 0, 10000), arg_int("max_level", 0, 10000)
    opp_min_level, opp_max_level = arg_int("opponent_min_level", 0, 10000), arg_int("opponent_max_level", 0, 10000)
    date_from, date_to = arg_date("from"), arg_date("to")
    up_from, up_to = arg_date("uploaded_from"), arg_date("uploaded_to")
    recorder = arg_int("recorder", 0)
    sort = arg_choice("sort", ("newest", "oldest", "uploaded"), "newest")
    limit = arg_int("limit", 1, MAX_LIMIT) or 100
    cursor = request.args.get("cursor")
    # offset: jump straight to a page (page 40 of a search without walking the 39 before it).
    # Bounded like the count: it never reads past the first 10,000 matches. Not with a cursor.
    offset = arg_int("offset", 0, COUNT_CAP) or 0
    if cursor and offset:
        abort(400, "offset or cursor, not both")
    want_count = request.args.get("count") == "1"

    conn = connect()
    cur = conn.cursor(dictionary=True)
    # A name becomes the accounts that used it (players table), then an id filter.
    def ids_named(text):
        if len(text) < 2:
            conn.close()
            abort(400, "a name needs at least 2 characters")
        cur.execute("SELECT DISTINCT steamid64 FROM player_names WHERE name LIKE ? LIMIT 200", (f"%{escape_like(text)}%",))
        return [r["steamid64"] for r in cur.fetchall()]
    name_ids = ids_named(name) if name else None
    opp_name_ids = ids_named(opp_name) if opp_name else None
    if name_ids == [] or opp_name_ids == []:
        conn.close()
        return jsonify({"replays": [], "next_cursor": None, **({"count": 0, "count_capped": False} if want_count else {})})
    sided = player is not None or character is not None or name_ids is not None or opponent is not None or \
        opp_char is not None or opp_name_ids is not None
    if result != "any" and not sided:
        conn.close()
        abort(400, "result needs a player, name or character")

    sort_col = "upload_datetime_" if sort == "uploaded" else "datetime_"
    desc = sort != "oldest"
    # Each "branch" is one side assignment: who is on side 1 and who on side 2. A query over
    # "either side" is two branches, each an index range read, merged by the outer ORDER BY.
    def branch(me):   # me = 1 or 2: the side `player` (or the searched character/name) is on
        other = 2 if me == 1 else 1
        w, v = [], []
        if player is not None:
            w.append(f"p{me}_steamid64 = ?"); v.append(player)
        if character is not None:
            w.append(f"p{me}_toon = ?"); v.append(character)
        if name_ids is not None:
            w.append(f"p{me}_steamid64 IN ({','.join('?' * len(name_ids))})"); v += name_ids
        if opponent is not None:
            w.append(f"p{other}_steamid64 = ?"); v.append(opponent)
        if opp_name_ids is not None:
            w.append(f"p{other}_steamid64 IN ({','.join('?' * len(opp_name_ids))})"); v += opp_name_ids
        if opp_char is not None:
            w.append(f"p{other}_toon = ?"); v.append(opp_char)
        if result == "win":
            w.append("winner = ?"); v.append(me - 1)
        elif result == "loss":
            w.append("winner = ?"); v.append(other - 1)
        if min_level is not None:
            w.append(f"p{me}_level >= ?"); v.append(min_level)
        if max_level is not None:
            w.append(f"p{me}_level <= ?"); v.append(max_level)
        if opp_min_level is not None:
            w.append(f"p{other}_level >= ?"); v.append(opp_min_level)
        if opp_max_level is not None:
            w.append(f"p{other}_level <= ?"); v.append(opp_max_level)
        return w, v

    common, cv = [], []
    if p1c is not None:
        common.append("p1_toon = ?"); cv.append(p1c)
    if p2c is not None:
        common.append("p2_toon = ?"); cv.append(p2c)
    if winner is not None:
        common.append("winner = ?"); cv.append(winner - 1)   # stored as 0 = side 1 won, 1 = side 2 won
    if not sided:
        if min_level is not None:
            common.append("p1_level >= ? AND p2_level >= ?"); cv += [min_level, min_level]
        if max_level is not None:
            common.append("p1_level <= ? AND p2_level <= ?"); cv += [max_level, max_level]
    for col, lo, hi in (("datetime_", date_from, date_to), ("upload_datetime_", up_from, up_to)):
        if lo is not None:
            common.append(f"{col} >= ?"); cv.append(lo)
        if hi is not None:
            common.append(f"{col} <= ?"); cv.append(hi)
    if recorder is not None:
        common.append("recorder_steamid64 = ?"); cv.append(recorder)
    page, pv = [], []
    if cursor:
        when, fname = decode_cursor(cursor)
        op = "<" if desc else ">"
        page.append(f"({sort_col} {op} ? OR ({sort_col} = ? AND filename {op} ?))"); pv += [when, when, fname]

    sides = [1, 2] if sided and side == "any" else [int(side)] if sided else [None]
    order = f"ORDER BY {sort_col} {'DESC' if desc else 'ASC'}, filename {'DESC' if desc else 'ASC'}"

    # The index each kind of question reads in order (the planner, left alone, sometimes walks
    # the whole table by date hoping to meet a player's rows soon: 10 s instead of 10 ms).
    def index_for(me):
        if me is None:
            return ""
        other = 2 if me == 1 else 1
        by_date = sort_col == "datetime_"
        mine = 1 if player is not None else len(name_ids) if name_ids is not None else 0
        theirs = 1 if opponent is not None else len(opp_name_ids) if opp_name_ids is not None else 0
        if mine and (not theirs or mine <= theirs):
            name = f"idx_p{me}_player" if character is not None and by_date else f"idx_p{me}_steam_date"
        elif theirs:   # the other side names fewer accounts: start from them
            name = f"idx_p{other}_player" if opp_char is not None and by_date else f"idx_p{other}_steam_date"
        elif character is not None and opp_char is not None and by_date:
            name = "idx_matchup"
        elif character is not None:
            name = f"idx_p{me}_toon_date" if by_date else f"idx_p{me}_toon_upload"
        elif opp_char is not None:
            name = f"idx_p{other}_toon_date" if by_date else f"idx_p{other}_toon_upload"
        else:
            return ""
        return f"FORCE INDEX ({name})"

    # Deferred join: the page is chosen from the index alone (date and filename; the filters on
    # columns the indexes carry are checked there too), then only its rows are read in full.
    parts, params = [], []
    for me in sides:
        w, v = branch(me) if me else ([], [])
        where = " AND ".join(w + common + page) or "TRUE"
        parts.append(f"(SELECT filename, {sort_col} FROM replay_metadata {index_for(me)} WHERE {where} {order} LIMIT ?)")
        params += v + cv + pv + [offset + limit + 1]
    # With the same person/character on both sides (a mirror), a replay can match both branches.
    if len(parts) == 1 and not offset:
        keys = parts[0]
    else:
        keys = f"SELECT * FROM ({' UNION '.join(parts)}) u {order} LIMIT ? OFFSET ?"
        params += [limit + 1, offset]
    cols = ", ".join("r." + c.strip() for c in COLUMNS.split(","))
    sql = f"SELECT {cols} FROM ({keys}) k JOIN replay_metadata r ON r.filename = k.filename " + order.replace("ORDER BY ", "ORDER BY r.").replace(", filename", ", r.filename")
    cur.execute(sql, params)
    rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = {"replays": [row_out(r) for r in rows], "next_cursor": encode_cursor(rows[-1], sort_col) if more and rows else None}
    if want_count:
        # One UNION (not a sum of the two sides): a mirror match must count once.
        cparts, cparams = [], []
        for me in sides:
            w, v = branch(me) if me else ([], [])
            cparts.append(f"(SELECT filename FROM replay_metadata {index_for(me)} WHERE {' AND '.join(w + common) or 'TRUE'} LIMIT {COUNT_CAP + 1})")
            cparams += v + cv
        cur.execute(f"SELECT COUNT(*) AS n FROM ({' UNION '.join(cparts)}) c", cparams)
        total = cur.fetchone()["n"]
        out["count"] = min(total, COUNT_CAP)
        out["count_capped"] = total > COUNT_CAP
    conn.close()
    return jsonify(out)


def escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@api_v2.route("/api/v2/players")
def players():
    """q=<name part> (2+ characters): accounts that used a name containing it, most replays
    first, with every name each one used and their replays per character ("characters":
    {"<toon>": replays}). character=<0-35> keeps players of that character."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        abort(400, "q needs at least 2 characters")
    character = arg_int("character", 0, 99)
    limit = arg_int("limit", 1, 100) or 25
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT DISTINCT steamid64 FROM player_names WHERE name LIKE ? LIMIT 500", (f"%{escape_like(q)}%",))
    ids = [r["steamid64"] for r in cur.fetchall()]
    out = []
    if ids:
        marks = ",".join("?" * len(ids))
        if character is None:
            cur.execute(f"SELECT steamid64, name, replays, last_played FROM players WHERE steamid64 IN ({marks}) ORDER BY replays DESC LIMIT ?", ids + [limit])
        else:
            cur.execute(f"SELECT p.steamid64, p.name, pc.replays, pc.last_played FROM players p JOIN player_characters pc ON pc.steamid64 = p.steamid64 "
                        f"WHERE p.steamid64 IN ({marks}) AND pc.toon = ? ORDER BY pc.replays DESC LIMIT ?", ids + [character, limit])
        found = cur.fetchall()
        names = names_of(cur, [r["steamid64"] for r in found])
        chars = {}
        if found:
            cur.execute(f"SELECT steamid64, toon, replays FROM player_characters WHERE steamid64 IN ({','.join('?' * len(found))})",
                        [r["steamid64"] for r in found])
            for r in cur.fetchall():
                chars.setdefault(r["steamid64"], {})[str(r["toon"])] = r["replays"]
        for r in found:
            out.append({"steamid64": str(r["steamid64"]), "name": r["name"], "replays": r["replays"],
                        "last_played": r["last_played"].strftime("%Y-%m-%d %H:%M:%S") if r["last_played"] else None,
                        "names": names.get(r["steamid64"], []), "characters": chars.get(r["steamid64"], {})})
    conn.close()
    return jsonify({"players": out})


def names_of(cur, ids):
    if not ids:
        return {}
    cur.execute(f"SELECT steamid64, name FROM player_names WHERE steamid64 IN ({','.join('?' * len(ids))}) ORDER BY replays DESC", ids)
    names = {}
    for r in cur.fetchall():
        names.setdefault(r["steamid64"], []).append(r["name"])
    return names


@api_v2.route("/api/v2/players/<int:steamid64>")
def player(steamid64):
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT steamid64, name, replays, uploads, last_played FROM players WHERE steamid64 = ?", (steamid64,))
    p = cur.fetchone()
    if not p:
        conn.close()
        abort(404, "no replays of this player")
    cur.execute("SELECT toon, replays, wins, max_level, last_level, last_played FROM player_characters WHERE steamid64 = ? ORDER BY replays DESC", (steamid64,))
    chars = [{"character": r["toon"], "replays": r["replays"], "wins": r["wins"], "max_level": r["max_level"], "last_level": r["last_level"],
              "last_played": r["last_played"].strftime("%Y-%m-%d %H:%M:%S") if r["last_played"] else None} for r in cur.fetchall()]
    names = names_of(cur, [steamid64]).get(steamid64, [])
    conn.close()
    return jsonify({"steamid64": str(steamid64), "name": p["name"], "names": names, "replays": p["replays"], "uploads": p["uploads"],
                    "last_played": p["last_played"].strftime("%Y-%m-%d %H:%M:%S") if p["last_played"] else None, "characters": chars})


@api_v2.route("/api/v2/players/<int:steamid64>/replays")
def player_replays(steamid64):
    """Every replay of one player on one character, in one answer (what building a bot reads, which took
    up to 400 pages of the list before): character (required), limit (max 20000, default 20000).
    Compact rows, newest first: [filename, side (1/2), opponent character, YYYYMMDD, own level,
    opponent level, 1 won / 0 lost]. Both sides are read from their covering indexes (idx_p1_player /
    idx_p2_player): the replay table itself is not touched."""
    character = arg_int("character", 0, 99)
    if character is None:
        abort(400, "character is required")
    limit = arg_int("limit", 1, 20000) or 20000
    conn = connect()
    cur = conn.cursor()
    rows = []
    for me, other in ((1, 2), (2, 1)):
        cur.execute(f"SELECT filename, p{other}_toon, datetime_, p{me}_level, p{other}_level, winner "
                    f"FROM replay_metadata FORCE INDEX (idx_p{me}_player) "
                    f"WHERE p{me}_steamid64 = ? AND p{me}_toon = ? ORDER BY datetime_ DESC LIMIT ?", (steamid64, character, limit))
        for filename, opp, when, mine, theirs, winner in cur.fetchall():
            rows.append((when, [filename, me, opp, int(when.strftime("%Y%m%d")) if when else 0, mine, theirs,
                                1 if winner == me - 1 else 0]))
    conn.close()
    rows.sort(key=lambda r: (r[0] is not None, r[0]), reverse=True)
    rows = [r[1] for r in rows[:limit]]
    return jsonify({"steamid64": str(steamid64), "character": character, "count": len(rows),
                    "columns": ["filename", "side", "opponent_character", "date", "level", "opponent_level", "won"],
                    "replays": rows})


@api_v2.route("/api/v2/characters/<int:toon>/players")
def character_players(toon):
    """Who plays a character. sort=replays|level|recent, min_replays, min_level, limit (max 200)."""
    sort = arg_choice("sort", ("replays", "level", "recent"), "replays")
    min_replays = arg_int("min_replays", 0) or 0
    min_level = arg_int("min_level", 0)
    limit = arg_int("limit", 1, 200) or 50
    order = {"replays": "pc.replays DESC", "level": "pc.last_level DESC, pc.replays DESC", "recent": "pc.last_played DESC"}[sort]
    where, v = ["pc.toon = ?", "pc.replays >= ?"], [toon, min_replays]
    if min_level is not None:
        where.append("pc.last_level >= ?"); v.append(min_level)
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute(f"SELECT pc.steamid64, p.name, pc.replays, pc.wins, pc.max_level, pc.last_level, pc.last_played FROM player_characters pc "
                f"JOIN players p ON p.steamid64 = pc.steamid64 WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?", v + [limit])
    out = [{"steamid64": str(r["steamid64"]), "name": r["name"], "replays": r["replays"], "wins": r["wins"], "max_level": r["max_level"],
            "last_level": r["last_level"], "last_played": r["last_played"].strftime("%Y-%m-%d %H:%M:%S") if r["last_played"] else None}
           for r in cur.fetchall()]
    conn.close()
    return jsonify({"character": toon, "players": out})


@api_v2.route("/api/v2/filenames")
def filenames():
    """after=<filename> (or nothing), limit (max 10000): filenames in order, for mirroring."""
    after = request.args.get("after", "")
    limit = arg_int("limit", 1, 10000) or 5000
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT filename FROM replay_metadata WHERE filename > ? ORDER BY filename LIMIT ?", (after, limit))
    names = [r[0] for r in cur.fetchall()]
    conn.close()
    return jsonify({"filenames": names, "next_after": names[-1] if len(names) == limit else None})


# ---- kept up to date by the upload endpoint (app.py calls this after a new replay is stored)

def record_summaries(cur, m):
    """One new replay: the players, their names and per-character summaries. m = the metadata
    dict of parse_replay_metadata plus p1_level / p2_level."""
    winner_side = m["winner"]   # 0 = side 1 won, 1 = side 2 won (as stored)
    if m.get("date1"):
        cur.execute("INSERT INTO activity_months (month, matches) VALUES (?, 1) ON DUPLICATE KEY UPDATE matches = matches + 1",
                    (m["date1"].strftime("%Y-%m"),))
    cur.execute("INSERT INTO activity_days (day, uploads) VALUES (UTC_DATE(), 1) ON DUPLICATE KEY UPDATE uploads = uploads + 1")
    # by Steam ID, so that two uploads touching the same two players (a rematch with sides
    # swapped) lock their rows in the same order: no deadlock between them
    for side in sorted((1, 2), key=lambda s: (m[f"p{s}_steamid64"] or 0, s)):
        sid = m[f"p{side}_steamid64"]
        if not sid:
            continue
        name = m[f"p{side}_name"]
        when = m["date1"]
        level = m.get(f"p{side}_level")
        won = 1 if winner_side == side - 1 else 0
        cur.execute("INSERT INTO players (steamid64, name, replays, last_played) VALUES (?, ?, 1, ?) "
                    "ON DUPLICATE KEY UPDATE replays = replays + 1, "
                    "name = IF(VALUES(last_played) >= last_played, VALUES(name), name), "
                    "last_played = GREATEST(last_played, VALUES(last_played))", (sid, name, when))
        cur.execute("INSERT INTO player_names (steamid64, name, replays, last_used) VALUES (?, ?, 1, ?) "
                    "ON DUPLICATE KEY UPDATE replays = replays + 1, last_used = GREATEST(last_used, VALUES(last_used))", (sid, name, when))
        cur.execute("INSERT INTO player_characters (steamid64, toon, replays, wins, max_level, last_level, last_played) VALUES (?, ?, 1, ?, ?, ?, ?) "
                    "ON DUPLICATE KEY UPDATE replays = replays + 1, wins = wins + VALUES(wins), "
                    "max_level = GREATEST(COALESCE(max_level, 0), COALESCE(VALUES(max_level), 0)), "
                    "last_level = IF(VALUES(last_played) >= last_played, COALESCE(VALUES(last_level), last_level), last_level), "
                    "last_played = GREATEST(last_played, VALUES(last_played))",
                    (sid, m[f"p{side}_toon"], won, level, level, when))
        ranked = 1 if m.get("p1_level") and m.get("p2_level") else 0   # both ranked: a ranked match
        cur.execute("INSERT INTO player_matchups (steamid64, toon, opp_toon, games, wins, ranked_games, ranked_wins, last_played) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?, ?) "
                    "ON DUPLICATE KEY UPDATE games = games + 1, wins = wins + VALUES(wins), ranked_games = ranked_games + VALUES(ranked_games), "
                    "ranked_wins = ranked_wins + VALUES(ranked_wins), last_played = GREATEST(last_played, VALUES(last_played))",
                    (sid, m[f"p{side}_toon"], m[f"p{3 - side}_toon"], won, ranked, won * ranked, when))
    # the player who uploaded it (migration 006): their row was just written above when they played in it
    if m.get("recorder_steamid64"):
        cur.execute("UPDATE players SET uploads = uploads + 1 WHERE steamid64 = ?", (m["recorder_steamid64"],))


SAFE_NAME = re.compile(r"^[A-Za-z0-9]{1,64}\.dat$")
BUNDLE_MAX = 100        # GET: the names have to fit in the URL
BUNDLE_MAX_POST = 1000  # POST: the names in the body


@api_v2.route("/api/v2/bundle", methods=["GET", "POST"])
def bundle():
    """files=<name>,<name>,... (up to 100): one tar holding each file exactly as stored
    (<name>.gz, gzip). Files that do not exist are left out (the tar lists what is there).
    One request instead of a hundred: no per-file request overhead, no decompression on the
    server, and the gzip copies are about 5 times smaller than the replays."""
    # POST (body: the names, one per line or comma separated): up to BUNDLE_MAX_POST in one
    # request, for a build that needs hundreds. The files go out exactly as stored (gzip each):
    # recompressing the whole set as one stream saves about a third of the bytes (xz) but costs
    # seconds of CPU per thousand replays on every request (measured 2026-11-17: 3.41 MB stored
    # .gz vs 2.21 MB xz -9 in 7.6 s, 2.53 MB zstd -19 in 5.3 s).
    if request.method == "POST":
        text = request.get_data(cache=False, as_text=True) or ""
        names = [n.strip() for n in re.split(r"[,\s]+", text) if n.strip()]
        limit = BUNDLE_MAX_POST
    else:
        names = [n for n in (request.args.get("files") or "").split(",") if n]
        limit = BUNDLE_MAX
    if not names or len(names) > limit or not all(SAFE_NAME.match(n) for n in names):
        abort(400, f"files: 1 to {limit} replay names (xxxx.dat)")
    folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for n in dict.fromkeys(names):
            path = os.path.join(folder, n + ".gz")
            if os.path.isfile(path):
                tar.add(path, arcname=n + ".gz", recursive=False)
    body = out.getvalue()
    headers = {"Cache-Control": "public, max-age=86400", "Vary": "Accept-Encoding"}
    # The tar's 512-byte headers and padding add about a fifth (4.19 MB for 1000 replays of
    # 3.41 MB); a fast gzip pass over the whole tar takes that back and a little more (3.29 MB)
    # for 0.08 s of CPU per thousand. The client asks for it and unpacks it (WinHTTP).
    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        body = gzip.compress(body, compresslevel=1)
        headers["Content-Encoding"] = "gzip"
    return Response(body, mimetype="application/x-tar", headers=headers)
