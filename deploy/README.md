# Replay server upgrade

Faster searches, each player's rank stored with their replays, player and statistics pages
(`/api/v2/...`), and two bug fixes. Everything that works today keeps answering exactly the same:
`/upload`, `/get_replays`, `/download` and the website (tested route by route on a full copy of
the database). The mod uses the new API only when it is there; it keeps working either way.

## Run it

Copy this folder to the server, then:

```
sudo bash upgrade.sh
```

That's all. Each change is shown with why it's done and what it costs, and asked one by one;
answer `n` to skip any of them. To make every change without being asked:

```
sudo bash upgrade.sh --all
```

It takes about 30 minutes, most of it reading the ranks out of the replay files. The
site stays up throughout, apart from a few seconds whenever a service restarts.

If anything stops it, run it again: finished steps are remembered and skipped. A log, the
backups and that memory are in `/root/replay-server-upgrade`.

## What it asks

| # | Change | Why | Cost |
|---|---|---|---|
| 1 | Back up the replay table | to put it back if anything goes wrong | ~1 min, ~100 MB of disk |
| 2 | Database cache 128 MB -> 1 GB | the table and its indexes are ~920 MB, so most searches read from disk. With 4,000 uploads coming in, a search took 20.5 s with 128 MB and 3.9 s with 1 GB | MariaDB restarts (~10 s). No cost: memory the server already has (~2.4 GB free) |
| 3 | Rank columns + small summary tables | where the ranks and the player summaries go | instant |
| 4 | New code | the new API and statistics; uploads store ranks; fixes an SQL injection in the website's name box and connections left open after a failed query | services restart (a few seconds) |
| 5 | Backend: 8 threads per worker | today 2 slow searches stop every upload and download until they finish | backend restarts (a few seconds) |
| 6 | Read the ranks from the stored replay files | the ranks of replays uploaded before today (new uploads store theirs from step 4 on) | ~20 min reading files at the lowest disk/CPU priority, ~2 min writing |
| 7 | Search indexes | a character's replays page 20: 114 s -> 0.7 s; a player's replays per character: 12.6 s -> 0.03 s | online, ~3 min, +60 MB |
| 8 | Work out the summaries | player pages, leaderboards and statistics read these small tables instead of the 1.4 M replays | online, ~2 min |

Then it checks the old routes and the new ones and prints how long each answer took. The new
`/api/v2` searches answer from step 7 on (the mod uses the old routes until then).

The new code needs step 3 and the summaries need step 6; skipping 1, 2 or 5 changes nothing else.
The ranks are read before the indexes are built on purpose: the other way round, every rank
written would update ten indexes one row at a time (days instead of minutes).

Timings above are from a full rehearsal on a copy of this database (1.46 M replays), from the
current code to the end, checked against a recount of every rank from the files.

## What it changes on the server

- `backend/`: `app.py` and `models.py` replaced, `api_v2.py` and `stats.py` added.
  `frontend/alt_front.py` replaced. Before replacing anything it checks the files are the versions
  this was made from (commit `6b986e8`), and stops to ask if one was changed since.
- `/etc/mysql/mariadb.conf.d/99-replay-server.cnf` (step 2: one setting).
- `/etc/systemd/system/bbcf-backend.service.d/replay-upgrade.conf` (step 5: the same start
  command plus `--threads 8`).
- Database `test_db`: two columns and new indexes on `replay_metadata`, and new small tables
  (`players`, `player_names`, `player_characters`, `player_matchups`, `stats_cache`,
  `activity_months`, `activity_days`).

No new services, no new packages, no open ports, no credentials stored anywhere.

## Undo

```
sudo bash upgrade.sh --undo
```

It asks for each part: the old code, the threads setting, the cache setting, and last the database
(columns, indexes and tables removed, old indexes back; online). The old code also works with the
new database in place, so putting the code back alone is enough to be exactly as before.

## If the database is not reachable as root

The script runs `mariadb test_db` as root (the Debian default lets root in without a password).
If that fails it stops before changing anything; give it the login instead:

```
sudo MYSQL_CMD="mariadb -u root -pPASSWORD" DUMP_CMD="mariadb-dump -u root -pPASSWORD" bash upgrade.sh
```

## If the server is set up differently

The script expects the app in `/root/flask_app/bbcf-im-replay-server`, the database `test_db`,
the backend answering on `http://127.0.0.1:5000` and the systemd services `bbcf-backend` and
`bbcf-frontend`. It checks all of these before changing anything and stops with the setting to
use if one does not match:

```
sudo APP_DIR=/path/to/app DB_NAME=test_db BACKEND_URL=http://127.0.0.1:5000 \
     BACKEND_UNIT=backend-service FRONTEND_UNIT=website-service bash upgrade.sh
```
