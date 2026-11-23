# Replay database: API v2, faster queries, levels

Changes to the replay server (backend + front end). Built and tested on a local copy (MariaDB
11.8.6 like the server, the same table, indexes and 128 MB cache, 1.46 M replays from the
2026-11-14 dump).

## What changes

**Nothing breaks.** `/upload`, `/get_replays`, `/download/<file>` and the Dash page keep working
exactly as now. `/get_replays` lists its columns instead of `SELECT *`, so its answer stays the
same after the new columns are added (tested: identical JSON to the current code).

1. **New endpoints** (`backend/api_v2.py`, a Flask blueprint registered in `app.py`):

   | Endpoint | What |
   |---|---|
   | `GET /api/v2/replays` | replays by any combination of: player (Steam ID, either side or one side), name (any name the account used), character, opponent (Steam ID or name), opponent character, sides, result (win/loss of the searched side), winner side, min/max level of either side, match date range, upload date range, recorder. Sort newest / oldest / newest upload. Pages by cursor (`next_cursor`), `count=1` adds the number of matches. |
   | `GET /api/v2/players?q=` | accounts that used a name like it: latest name, every other name, replays, replays per character |
   | `GET /api/v2/players/<steamid>` | one player: names, per character replays, wins, highest and latest level, last played |
   | `GET /api/v2/characters/<c>/players` | who plays a character, by replays, level or last played (e.g. the best Ragna players) |
   | `GET /api/v2/players/<steamid>/replays?character=` | every replay of a player on one character in one answer, compact rows (what a bot build reads: one request instead of up to 400 pages) |
   | `GET /api/v2/filenames?after=` | every filename in order, 10,000 at a time (for mirrors) |
   | `GET/POST /api/v2/bundle` | up to 100 replay files by GET, 1,000 by POST, in one tar of the stored .gz files |
   | `GET /api/v2/stats/overview` | totals, replays per character and matchup, players by rank, matches per month (`stats.py`) |
   | `GET /api/v2/directory` | every player, sortable and filterable: the player list and every leaderboard in one |
   | `GET /api/v2/players/<steamid>/matchups`, `/history` | a player's record per matchup; rank and results over time |
   | `GET /api/v2/leaderboard` | the best players of a character, overall or against one character |

2. **Database** (`migrations/`, run in this order by `deploy/upgrade.sh`):
   - `001a_columns.sql`: columns `p1_level`, `p2_level` and the small summary tables
     (`players`, `player_names`, `player_characters`). Instant, no table rebuild.
   - `deploy/read_levels.py`: both players' ranks read from the replay files (only their first
     bytes), loaded 20,000 rows at a time.
   - `001b_indexes.sql`: indexes shaped for each kind of search, replacing four
     single-column indexes they start with. Online (`ALGORITHM=INPLACE, LOCK=NONE`): reads and
     uploads keep working.
   - `002_fill_summaries.sql`, `003_level_summaries.sql`, `004_stats.sql`, `005_uploads.sql`: the summary tables filled from the stored replays (a few minutes in all).
   - About a third of replays carry no level (both sides 0 in the file: player matches); those stay NULL.

3. **Uploads** record the levels and update the summary tables (a failure there never fails the
   upload).

4. **Front end fix (security):** the name box was pasted into the SQL
   (`p1 LIKE '%{p1}%'`): a name with a quote in it can run arbitrary SQL against the database.
   Now a parameter. Worth applying even without the rest.

5. **Server setting (biggest single speed-up, no code):** `innodb_buffer_pool_size` is the
   default 128 MB while the table and its indexes are ~920 MB, so most queries read the disk.
   The VM has 4 GB with ~2.4 GB free; 1 GB would hold everything:
   `/etc/mysql/mariadb.conf.d/50-server.cnf`, `[mysqld] innodb_buffer_pool_size = 1G`, restart
   MariaDB. Costs nothing.

## Speed (local copy, 128 MB cache like the server)

Milliseconds, cold cache (just restarted) / warm, on the local copy with the server's 128 MB
cache, measured while the PC was busy with other work (so real numbers should be lower).

| Question | Today (existing queries) | API v2 |
|---|---|---|
| a player's replays, page of 500 | 3,530 / 683 | 11 / 10 |
| a player's replays on one character, newest 100 | 2,896 / 4,414 | 132 / 69 |
| how many replays a player has on each character | 12,093 / 12,571 (36 queries) | 200 / 31 (one) |
| a character's replays, page 20 | 116,078 / 114,205 | ~1,500 / ~700 for any page |
| a character's replay count | 4,505 / 6,638 | 279 / 238 |
| name contains "heythan", first page | 5,951 / 4,396 | 3,949 / 470 |
| players whose name is like "heyth" | 6,718 / 7,175 | 326 / 144 |
| Ragna vs Kokonoe, both sides ranked 100+ | (no such filter) | 57 / 71 |

Why: pages continue from a cursor (no OFFSET); each kind of question reads one index in order
(the API names it: left alone the planner sometimes walks the whole table by date hoping to meet
a player's rows early: 10 s instead of 10 ms); the page is picked from the index alone and only
its rows are read (deferred join); opponent character, ranks and winner sit inside the indexes, so
those filters never read rows; names are looked up in a 28,000-row table, not 1.5 M replays.

**Bounded load.** Every v2 query stops after 5 seconds (`max_statement_time`) and answers 503
"add a player or a character" instead of keeping the database busy; counts stop at 10,000
("10,000+"). The website's queries get a 15 s limit and a message instead of running for
minutes (one of today's name searches ran 13 minutes on the local copy).

**Downloads.** `/download` sends the stored .gz as it is to clients that accept gzip (browsers,
the mod): no decompression on the server and ~4 KB instead of 64 KB per replay; clients that do
not ask get exactly what they get today. `/api/v2/bundle` sends up to 100 replays in one answer:
100 replays = 430 KB in one request instead of 6.5 MB in 100.

## Cost

No new services, no new storage worth mentioning (+~60 MB of indexes, three small tables). Every
v2 query reads an index range instead of scanning, so the database does less work than today for
the same questions. Rate limiting stays as it is (30 per minute per address).

## Bugs found in the current code (fixed here)

- Front end name box pasted into SQL (injection).
- A failed query left its connection open with an open transaction (no close on error, no
  autocommit): that transaction holds the table's metadata lock until the process ends, and a
  schema change waiting on it makes every later query wait too. Now autocommit and always closed.

## Rollout

`deploy/README.md`: `deploy/make_package.sh` builds one archive; on the server `sudo bash
upgrade.sh` backs up the table and the files it replaces, then asks before each step (with what it
does and what it costs), remembers finished steps if stopped, and `upgrade.sh --undo` puts the
code, the settings and the database back as they were. About 30 minutes, the site up throughout
apart from a few seconds per service restart. Backend: `gunicorn -w 2 --threads 8` lets downloads go
on while a slow query runs (today 2 workers, so two slow queries stop every download).

## Tests (`tests/`)

`tests/test_api.py` against the local copy:
1. 62 random combinations of every filter, every page followed by cursor, compared row by row
   (and in order) with the same filter worked out in Python over all 1.46 M replays; counts too.
2. Players, per-character summaries (25 random players against a full recount), filename walk.
3. `/get_replays` answers compared with the current server code on the same database.
4. Upload: a replay removed and uploaded again comes back identical plus its ranks; summaries
   move by exactly one; the same upload again changes nothing.
5. Downloads: plain download byte-identical to the current code; gzip download decodes to the
   same bytes; bundles hold exactly the stored files; bad names and lists over 100 refused.
The timings above were measured on the local copy with the server's 128 MB cache.
