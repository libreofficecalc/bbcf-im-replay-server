"""Statistics for the replay browser and website (API v2): players, matchups, leaderboards.

Registered in app.py next to api_v2. Everything here reads the small summary tables
(players 23k rows, player_characters 61k, player_matchups ~300k), never scans the replays,
except:
  - /api/v2/stats/overview: database-wide numbers, worked out at most once an hour by the
    first request that finds them stale (one at a time: GET_LOCK) and read from stats_cache by
    everyone else, so a thousand visitors cost what one does;
  - /api/v2/players/<id>/history: one player's own replays, read from the player index only.

Characters are numbers (the game's order: 0 Ragna ... 35 Es); ranks are the visible rank,
1-35 = LV1-LV35, 36 Leader, 37 Hero, 38 Kisshin, 39 Hades, 40 Ruler. Win rates come with the
games they are from; leaderboards sort by a confidence-adjusted rate (the lower end of the Wilson
interval) so that 3 wins out of 3 does not beat 70 out of 100.
"""
import json
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, abort
import mariadb

from api_v2 import connect, arg_int

stats = Blueprint("stats", __name__)

CACHE_MINUTES = 60
CHARACTERS = 36

# Lower end of the 95% Wilson interval of wins / games, in SQL (z = 1.96).
WILSON = ("(({w}) / ({g}) + 1.9208 / ({g}) - 1.96 * SQRT(({w}) / ({g}) * (1 - ({w}) / ({g})) / ({g}) + 0.9604 / (({g}) * ({g})))) "
          "/ (1 + 3.8416 / ({g}))")


def wilson(w, g):
    return WILSON.format(w=w, g=g)


def rows(cur):
    cols = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        d = {}
        for k, v in zip(cols, r):
            if isinstance(v, datetime):
                v = v.isoformat(sep=" ")
            elif hasattr(v, "__float__") and not isinstance(v, (int, float)):   # SUM() gives Decimal
                v = int(v) if v == int(v) else float(v)
            d[k] = v
        out.append(d)
    return out


# ---- database-wide numbers ------------------------------------------------------------------

def build_overview(cur):
    """Every database-wide number, in one JSON. Run at most once an hour."""
    cur.execute("SET SESSION max_statement_time = 30")
    o = {"made": datetime.utcnow().isoformat(sep=" ")}
    cur.execute("SELECT month, matches FROM activity_months ORDER BY month")
    months = [[m, n] for m, n in cur.fetchall()]
    o["replays"] = sum(n for _, n in months)
    o["matches_per_month"] = months
    cur.execute("SELECT COUNT(*), SUM(country IS NOT NULL) FROM players"); n, c = cur.fetchone()
    o["players"] = n; o["players_with_country"] = int(c or 0)
    cur.execute("SELECT COUNT(DISTINCT steamid64) FROM player_characters WHERE max_level IS NOT NULL")
    o["ranked_players"] = cur.fetchone()[0]
    cur.execute("SELECT day, uploads FROM activity_days WHERE day >= UTC_DATE() - INTERVAL 60 DAY ORDER BY day")
    days = [[str(d), n] for d, n in cur.fetchall()]
    o["uploads_per_day"] = days
    today = datetime.utcnow().date()
    for span in (1, 7, 30):
        o["uploads_%dd" % span] = sum(n for d, n in days if (today - datetime.strptime(d, "%Y-%m-%d").date()).days < span)
    # characters: appearances (a mirror match counts twice), wins, players
    cur.execute("SELECT toon, SUM(games) g, SUM(wins) w FROM player_matchups GROUP BY toon")
    per = {t: {"character": t, "games": int(g), "wins": int(w)} for t, g, w in cur.fetchall()}
    cur.execute("SELECT toon, COUNT(*) players, SUM(max_level >= 36) top_players FROM player_characters GROUP BY toon")
    for t, p, tp in cur.fetchall():
        per.setdefault(t, {"character": t, "games": 0, "wins": 0}).update(players=p, top_players=int(tp or 0))
    o["characters"] = sorted(per.values(), key=lambda x: -x["games"])
    # every matchup: games and wins of the row character against the column character
    # [character, opponent, games, wins, ranked games, ranked wins]
    cur.execute("SELECT toon, opp_toon, SUM(games), SUM(wins), SUM(ranked_games), SUM(ranked_wins) FROM player_matchups GROUP BY toon, opp_toon")
    o["matchups"] = [[t, ot, int(g), int(w), int(rg), int(rw)] for t, ot, g, w, rg, rw in cur.fetchall()]
    # players by highest rank on any character
    cur.execute("SELECT m, COUNT(*) FROM (SELECT steamid64, MAX(max_level) m FROM player_characters "
                "WHERE max_level IS NOT NULL GROUP BY steamid64) s GROUP BY m ORDER BY m")
    o["players_by_rank"] = [[m, n] for m, n in cur.fetchall()]
    # the players with the most replays
    cur.execute("SELECT p.steamid64, p.name, p.replays FROM players p ORDER BY p.replays DESC LIMIT 10")
    o["most_replays"] = rows(cur)
    return o


@stats.route("/api/v2/stats/overview")
def overview():
    cur = connect().cursor()
    cur.execute("SELECT made, body FROM stats_cache WHERE name = 'overview'")
    have = cur.fetchone()
    if have and have[0] > datetime.utcnow() - timedelta(minutes=CACHE_MINUTES):
        return cached(have[1])
    # Stale or missing: one request rebuilds it, the others serve the old one meanwhile.
    cur.execute("SELECT GET_LOCK('stats_overview', 0)")
    if cur.fetchone()[0] != 1:
        if have:
            return cached(have[1])
        abort(503, "the statistics are being worked out, try again in a minute")
    try:
        body = json.dumps(build_overview(cur), separators=(",", ":"))
        cur.execute("REPLACE INTO stats_cache (name, made, body) VALUES ('overview', ?, ?)", (datetime.utcnow(), body))
    finally:
        cur.execute("DO RELEASE_LOCK('stats_overview')")
    return cached(body)


def cached(body):
    from flask import Response
    return Response(body, mimetype="application/json", headers={"Cache-Control": "public, max-age=300"})


# ---- players --------------------------------------------------------------------------------

# Upper end of the Wilson interval: for "worst first", so that 0 out of 2 does not come before
# 300 out of 1000.
WILSON_UP = ("(({w}) / ({g}) + 1.9208 / ({g}) + 1.96 * SQRT(({w}) / ({g}) * (1 - ({w}) / ({g})) / ({g}) + 0.9604 / (({g}) * ({g})))) "
             "/ (1 + 3.8416 / ({g}))")

DIRECTORY_SORTS = {   # column, its direction when "desc" is asked for
    "games": "games {d}",
    "win_rate": "score {d}",
    "rank": "max_level IS NULL, max_level {d}, games DESC",
    "recent": "last_played {d}",
    "name": "name {d}",
    "characters": "n_chars {d}, games DESC",
    "uploads": "uploads {d}, games DESC",   # replays they uploaded themselves (migration 006)
}


@stats.route("/api/v2/directory")
def directory():
    """Every player, sortable and filterable: the player list and every leaderboard in one.
    character (numbers on that character only), opponent (numbers against that character only),
    ranked (1: only games where both players had a rank), min_games, min_rank / max_rank (the
    highest rank reached, on the character when one is given), active_days, q (any name they
    used contains it), plays (comma list of characters: also everyone who played one of them; the
    search box sends it when the text names a character), sort (games, win_rate, rank, recent,
    name, characters, uploads), order (desc, asc), limit (<= 200), offset, steam (comma list of Steam IDs,
    up to 100: only those accounts).
    Each player comes with every character they play (most played first) and their other names."""
    character = arg_int("character", 0, CHARACTERS - 1)
    opponent = arg_int("opponent", 0, CHARACTERS - 1)
    ranked = request.args.get("ranked", "0") == "1"
    min_games = arg_int("min_games", 0) or 0
    min_rank, max_rank = arg_int("min_rank", 1, 40), arg_int("max_rank", 1, 40)
    active_days = arg_int("active_days", 1)
    sort = request.args.get("sort", "games")
    if sort not in DIRECTORY_SORTS:
        abort(400, "sort: " + ", ".join(DIRECTORY_SORTS))
    desc = request.args.get("order", "desc") != "asc"
    limit = min(arg_int("limit", 1) or 50, 200)
    offset = min(arg_int("offset", 0) or 0, 100000)
    q = (request.args.get("q") or "").strip()
    plays = []
    for t in (request.args.get("plays") or "").split(","):
        if t.strip().isdigit() and int(t) < CHARACTERS:
            plays.append(int(t))
    # steam: only these accounts (comma list, up to 100): a Steam ID typed in the search, or the
    # asker's Steam friends a chunk at a time.
    steams = []
    for t in (request.args.get("steam") or "").split(","):
        if t.strip().isdigit():
            steams.append(int(t))
    steams = steams[:100]

    # Games and wins per player: from the per-character summary when that is enough (all games,
    # any opponent), else from the per-matchup one.
    if not ranked and opponent is None:
        src = ("SELECT steamid64, SUM(replays) games, SUM(wins) wins, MAX(last_played) last_played "
               "FROM player_characters" + (" WHERE toon = ?" if character is not None else "") + " GROUP BY steamid64")
        args = [character] if character is not None else []
    else:
        g_col, w_col = ("ranked_games", "ranked_wins") if ranked else ("games", "wins")
        where, args = [], []
        if character is not None:
            where.append("toon = ?"); args.append(character)
        if opponent is not None:
            where.append("opp_toon = ?"); args.append(opponent)
        src = (f"SELECT steamid64, SUM({g_col}) games, SUM({w_col}) wins, MAX(last_played) last_played FROM player_matchups"
               + (" WHERE " + " AND ".join(where) if where else "") + " GROUP BY steamid64")
    # Rank and how many characters: on the character, or over all of them.
    if character is not None:
        rank_src = "SELECT steamid64, max_level, 1 n_chars FROM player_characters WHERE toon = ?"
        rank_args = [character]
    else:
        rank_src = "SELECT steamid64, MAX(max_level) max_level, COUNT(*) n_chars FROM player_characters GROUP BY steamid64"
        rank_args = []
    where, wargs = ["s.games >= ?", "s.games > 0"], [min_games]
    if min_rank is not None:
        where.append("r.max_level >= ?"); wargs.append(min_rank)
    if max_rank is not None:
        where.append("r.max_level <= ?"); wargs.append(max_rank)
    if active_days:
        where.append("s.last_played >= ?"); wargs.append(datetime.utcnow() - timedelta(days=active_days))
    # A name search lists the closest matches first: a name that is the text, then one that
    # starts with it, then one that contains it (any name the player used); the sort chosen
    # orders each group. Players found only through a character's name ("plays") come last.
    match_src, match_args = "", []
    if q:
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match_src = (" LEFT JOIN (SELECT steamid64, MIN(CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END) closeness "
                     "FROM player_names WHERE name LIKE ? GROUP BY steamid64) m ON m.steamid64 = s.steamid64")
        match_args = [q, like + "%", "%" + like + "%"]
    if q or plays:
        any_of = []
        if q:
            any_of.append("m.steamid64 IS NOT NULL")
        if plays:
            any_of.append("s.steamid64 IN (SELECT steamid64 FROM player_characters WHERE toon IN (" + ",".join("?" * len(plays)) + "))")
            wargs += plays
        where.append("(" + " OR ".join(any_of) + ")")
    if steams:
        where.append("s.steamid64 IN (" + ",".join("?" * len(steams)) + ")")
        wargs += steams
    score = (WILSON if desc else WILSON_UP).format(w="s.wins", g="s.games")
    sql = ("SELECT * FROM (SELECT s.steamid64, p.name, p.country, p.uploads, s.games, s.wins, s.last_played, r.max_level, r.n_chars, "
           + score + " AS score, " + ("CASE WHEN p.name = ? THEN -1 ELSE COALESCE(m.closeness, 3) END" if q else "0") + " AS closeness, "
           "COUNT(*) OVER () AS total "
           "FROM (" + src + ") s JOIN players p ON p.steamid64 = s.steamid64 LEFT JOIN (" + rank_src + ") r ON r.steamid64 = s.steamid64"
           + match_src + " WHERE " + " AND ".join(where) + ") t ORDER BY closeness, " + DIRECTORY_SORTS[sort].format(d="DESC" if desc else "ASC")
           + ", steamid64 LIMIT ? OFFSET ?")
    if q:
        args = [q] + args   # the select's own "is it their current name" comes first
    conn = connect()
    cur = conn.cursor()
    # MariaDB (seen on 11.8.6) gets this query wrong with "split materialization" (the
    # grouped summary joined LATERAL, per player): with a name search and ranked=1 it kept one
    # player out of 52. Off for this query, which reads the summaries whole anyway.
    cur.execute("SET SESSION optimizer_switch = 'split_materialized=off'")
    cur.execute(sql, args + rank_args + match_args + wargs + [limit, offset])
    out = rows(cur)
    total = out[0]["total"] if out else 0
    ids = [r["steamid64"] for r in out]
    chars, names = {}, {}
    if ids:
        marks = ",".join("?" * len(ids))
        # every character of these players, most played first: [character, games, wins, highest rank]
        cur.execute(f"SELECT steamid64, toon, replays, wins, max_level FROM player_characters WHERE steamid64 IN ({marks}) "
                    "ORDER BY steamid64, replays DESC", ids)
        for sid, t, n, w, ml in cur.fetchall():
            chars.setdefault(sid, []).append([t, n, w, ml])
        cur.execute(f"SELECT steamid64, name FROM player_names WHERE steamid64 IN ({marks}) ORDER BY steamid64, last_used DESC", ids)
        for sid, n in cur.fetchall():
            names.setdefault(sid, []).append(n)

    for r in out:
        sid = r["steamid64"]
        r.pop("total", None); r.pop("score", None); r.pop("closeness", None)
        r["characters"] = chars.get(sid, [])
        r["names"] = [n for n in names.get(sid, []) if n != r["name"]]
        r["steamid64"] = str(sid)
    return jsonify({"total": total, "offset": offset, "players": out})


@stats.route("/api/v2/players/<int:steamid>/matchups")
def player_matchups(steamid):
    """One player's record against every character, for each character they play (or one)."""
    character = arg_int("character", 0, CHARACTERS - 1)
    cur = connect().cursor()
    sql = "SELECT toon, opp_toon, games, wins, ranked_games, ranked_wins, last_played FROM player_matchups WHERE steamid64 = ?"
    args = [steamid]
    if character is not None:
        sql += " AND toon = ?"; args.append(character)
    cur.execute(sql + " ORDER BY toon, games DESC", args)
    return jsonify({"steamid64": str(steamid), "matchups": rows(cur)})


@stats.route("/api/v2/players/<int:steamid>/history")
def player_history(steamid):
    """How a player's rank and results moved over their replays: up to <points> points (default
    120), each the last rank seen, the highest rank in that slice of time, and its games/wins.
    character optional; year optional (only that year's replays). Also every year they played,
    with the highest and last rank of that year. Reads only the player index (steam id,
    character, date, levels, winner)."""
    character = arg_int("character", 0, CHARACTERS - 1)
    points = min(arg_int("points", 10) or 120, 400)
    year = arg_int("year", 2000, 2100)
    cur = connect().cursor()
    parts = []; args = []
    for side, other in ((1, 2), (2, 1)):
        where = f"p{side}_steamid64 = ?" + (f" AND p{side}_toon = ?" if character is not None else "")
        parts.append(f"SELECT datetime_ dt, p{side}_toon toon, p{side}_level lvl, IF(winner = {side - 1}, 1, 0) won "
                     f"FROM replay_metadata FORCE INDEX (idx_p{side}_player) WHERE {where}")
        args += [steamid] + ([character] if character is not None else [])
    cur.execute(" UNION ALL ".join(parts) + " ORDER BY dt", args)
    games = [g for g in cur.fetchall() if g[0] is not None]
    years = {}
    for dt, toon, lvl, won in games:
        y = years.setdefault(dt.year, {"year": dt.year, "games": 0, "wins": 0, "best_rank": None, "last_rank": None})
        y["games"] += 1; y["wins"] += won
        if lvl is not None:
            y["best_rank"] = max(y["best_rank"] or 0, lvl); y["last_rank"] = lvl
    if year is not None:
        games = [g for g in games if g[0].year == year]
    out_years = [years[k] for k in sorted(years)]
    if not games:
        return jsonify({"steamid64": str(steamid), "games": 0, "points": [], "years": out_years})
    per = max(1, -(-len(games) // points))
    out = []
    for i in range(0, len(games), per):
        chunk = games[i:i + per]
        lv = [g[2] for g in chunk if g[2] is not None]
        out.append({"until": chunk[-1][0].isoformat(sep=" "), "games": len(chunk), "wins": sum(g[3] for g in chunk),
                    "rank": lv[-1] if lv else None, "best_rank": max(lv) if lv else None})
    return jsonify({"steamid64": str(steamid), "games": len(games), "first": games[0][0].isoformat(sep=" "),
                    "points": out, "years": out_years})


# ---- leaderboards ---------------------------------------------------------------------------

@stats.route("/api/v2/leaderboard")
def leaderboard():
    """The best players of a character, overall or against one character.
    character (required), opponent (optional), min_games (default 30), min_rank (the player's
    highest rank on the character; 1 = any ranked player), sort (win_rate, games, rank),
    limit (<= 100), offset."""
    character = arg_int("character", 0, CHARACTERS - 1)
    if character is None:
        abort(400, "character is required")
    opponent = arg_int("opponent", 0, CHARACTERS - 1)
    min_games = max(arg_int("min_games", 1) or 30, 1)
    min_rank = arg_int("min_rank", 1, 40)
    sort = request.args.get("sort", "win_rate")
    order = {"win_rate": "score DESC", "games": "games DESC", "rank": "max_level IS NULL, max_level DESC, score DESC"}.get(sort)
    if not order:
        abort(400, "sort: win_rate, games, rank")
    limit = min(arg_int("limit", 1) or 50, 100)
    offset = min(arg_int("offset", 0) or 0, 10000)
    cur = connect().cursor()
    # games=ranked (default): only ranked matches (both sides ranked); all: player matches too,
    # where a good player beating friends 200-10 says little.
    ranked = request.args.get("games", "ranked") != "all"
    g_col, w_col = ("ranked_games", "ranked_wins") if ranked else ("games", "wins")
    if opponent is not None:
        src = (f"SELECT pm.steamid64, pm.{g_col} games, pm.{w_col} wins, pm.last_played FROM player_matchups pm "
               f"WHERE pm.toon = ? AND pm.opp_toon = ? AND pm.{g_col} >= ?")
        args = [character, opponent, min_games]
    elif ranked:
        src = ("SELECT pm.steamid64, SUM(pm.ranked_games) games, SUM(pm.ranked_wins) wins, MAX(pm.last_played) last_played "
               "FROM player_matchups pm WHERE pm.toon = ? GROUP BY pm.steamid64 HAVING games >= ?")
        args = [character, min_games]
    else:
        src = ("SELECT pc.steamid64, pc.replays games, pc.wins, pc.last_played FROM player_characters pc "
               "WHERE pc.toon = ? AND pc.replays >= ?")
        args = [character, min_games]
    sql = ("SELECT s.steamid64, p.name, p.country, s.games, s.wins, s.last_played, pc.max_level, pc.last_level, "
           + wilson("s.wins", "s.games") + " AS score, COUNT(*) OVER () AS total "
           "FROM (" + src + ") s JOIN players p ON p.steamid64 = s.steamid64 "
           "LEFT JOIN player_characters pc ON pc.steamid64 = s.steamid64 AND pc.toon = ? "
           + ("WHERE pc.max_level >= ? " if min_rank else "") +
           "ORDER BY " + order + ", s.steamid64 LIMIT ? OFFSET ?")
    cur.execute(sql, args + [character] + ([min_rank] if min_rank else []) + [limit, offset])
    out = rows(cur)
    total = out[0]["total"] if out else 0
    for r in out:
        r.pop("total", None)
        r["score"] = round(r["score"], 4)
        r["steamid64"] = str(r["steamid64"])
    return jsonify({"character": character, "opponent": opponent, "min_games": min_games, "ranked_only": ranked,
                    "total": total, "players": out})
