#!/bin/bash
# Replay server upgrade: faster searches, rank data, player statistics (API v2).
#
#   sudo bash upgrade.sh          shows each change with why and what it costs, asks before each
#   sudo bash upgrade.sh --all    makes every change without asking
#   sudo bash upgrade.sh --undo   puts back what this script changed (asks before each part)
#
# Safe to stop and run again: finished steps are remembered (in /root/replay-server-upgrade) and
# skipped. Nothing that works today stops working: /upload, /get_replays, /download and the
# website answer exactly as before.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${APP_DIR:-/root/flask_app/bbcf-im-replay-server}"
DB="${DB_NAME:-test_db}"
MYSQL="${MYSQL_CMD:-mariadb}"
DUMP="${DUMP_CMD:-mariadb-dump}"
CNF_DIR="${CNF_DIR:-/etc/mysql/mariadb.conf.d}"
STATE="${STATE_DIR:-/root/replay-server-upgrade}"
URL="${BACKEND_URL:-http://127.0.0.1:5000}"
SYSTEMCTL="${SYSTEMCTL_CMD:-systemctl}"
BACKEND_UNIT="${BACKEND_UNIT:-bbcf-backend}"     # the systemd services that run the backend and the website
FRONTEND_UNIT="${FRONTEND_UNIT:-bbcf-frontend}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
REPLAYS="${REPLAYS_DIR:-$APP/replays}"
PY="${PYTHON:-python3}"

ALL=0; UNDO=0
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    --undo) UNDO=1 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done

B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'
mkdir -p "$STATE/done" "$STATE/backup"
LOG="$STATE/upgrade.log"
exec > >(tee -a "$LOG") 2>&1
echo; echo "${D}---- $(date '+%F %T') upgrade.sh $*${N}"

say()  { echo "$@"; }
ok()   { echo "${G}  done${N} $*"; }
warn() { echo "${Y}  $*${N}"; }
die()  { echo "${R}STOPPED: $*${N}"; echo "Nothing after this point was changed. Log: $LOG"; exit 1; }
done_() { touch "$STATE/done/$1"; }
is_done() { [ -e "$STATE/done/$1" ]; }
sql()  { $MYSQL "$DB" "$@"; }
secs() { local s=$1; printf '%dm%02ds' $((s/60)) $((s%60)); }

# ask "title" "why" "cost" -> 0 = yes
ask() {
  echo
  echo "${B}== $1${N}"
  echo "   Why:  $2"
  echo "   Cost: $3"
  [ $ALL = 1 ] && { echo "   (--all: yes)"; return 0; }
  local a; read -r -p "   Do it? [Y/n] " a </dev/tty
  [[ -z "$a" || "$a" =~ ^[Yy] ]]
}

restart_web() {
  $SYSTEMCTL restart "$BACKEND_UNIT" "$FRONTEND_UNIT" || die "could not restart $BACKEND_UNIT / $FRONTEND_UNIT"
  for i in $(seq 1 30); do curl -s -o /dev/null "$URL/get_replays?page_size=1" && return 0; sleep 1; done
  die "the backend does not answer on $URL after the restart (journalctl -u $BACKEND_UNIT)"
}

# ---------------------------------------------------------------------------------------------
undo() {
  say "${B}Undo${N}: each part is asked separately. Backups: $STATE/backup"
  if [ -e "$STATE/backup/code.tar.gz" ] && ask "Put the old code back" \
      "Back to the code from before the upgrade (backend/app.py, models.py, frontend/alt_front.py; api_v2.py and stats.py removed)." \
      "Both web services restart: a few seconds without answers."; then
    rm -f "$APP/backend/api_v2.py" "$APP/backend/stats.py"
    tar -xzf "$STATE/backup/code.tar.gz" -C "$APP" || die "could not unpack the code backup"
    rm -f "$STATE/done/code"; restart_web; ok "old code running"
  fi
  if [ -e "$UNIT_DIR/$BACKEND_UNIT.service.d/replay-upgrade.conf" ] && ask "Remove the backend threads setting" \
      "Back to gunicorn -w 2 without threads." "The backend restarts: a few seconds without answers."; then
    rm -f "$UNIT_DIR/$BACKEND_UNIT.service.d/replay-upgrade.conf"; $SYSTEMCTL daemon-reload
    rm -f "$STATE/done/threads"; restart_web; ok "threads setting removed"
  fi
  if [ -e "$CNF_DIR/99-replay-server.cnf" ] && ask "Put the database cache back to 128 MB" \
      "Removes $CNF_DIR/99-replay-server.cnf." "MariaDB restarts: about 10 seconds without uploads or searches."; then
    rm -f "$CNF_DIR/99-replay-server.cnf"; $SYSTEMCTL restart mariadb || die "MariaDB did not restart"
    rm -f "$STATE/done/cache"; restart_web; ok "cache back to the default"
  fi
  if ask "Remove the new tables, columns and indexes" \
      "Only needed if you want the database exactly as before; the old code works with them in place." \
      "Online (reads and uploads keep working), a few minutes. The rank data is lost (it can be read from the files again)."; then
    [ -e "$STATE/done/code" ] && die "the new code is still running: put the old code back first"
    # (the old code can leave a connection open that holds the table for a while: wait and try again)
    n=0
    until { echo "SET SESSION lock_wait_timeout = 10;"; cat "$HERE/sql/undo.sql"; } | sql; do
      n=$((n + 1)); [ $n -ge 5 ] && die "undo.sql failed (see above); run --undo again"
      warn "the table was busy; trying again in 20 s ($n/5)"; sleep 20
    done
    rm -f "$STATE/done/schema" "$STATE/done/levels" "$STATE/done/indexes" "$STATE/done/summaries"; ok "database schema as before"
  fi
  say; say "${G}Undo finished.${N}"
  exit 0
}

# ---------------------------------------------------------------------------------------------
say "${B}Replay server upgrade${N}"
say "App: $APP   Database: $DB   State and backups: $STATE"

# Checks: nothing is changed by these.
[ "$(id -u)" = 0 ] || [ -n "${ALLOW_NON_ROOT:-}" ] || die "run it as root: sudo bash $0"
[ -d "$APP/backend" ] && [ -d "$APP/frontend" ] || die "$APP/backend or /frontend not found (set APP_DIR=...)"
sql -N -e "SELECT 1" >/dev/null 2>&1 || die "cannot reach the database as '$MYSQL $DB' (set MYSQL_CMD=\"mariadb -u USER -pPASSWORD\")"
for u in "$BACKEND_UNIT" "$FRONTEND_UNIT"; do
  $SYSTEMCTL cat "$u" >/dev/null 2>&1 || die "no systemd service '$u' (set BACKEND_UNIT=... FRONTEND_UNIT=... to the names this server uses)"
done
[ $UNDO = 1 ] || curl -s -o /dev/null "$URL/get_replays?page_size=1" || die "the backend does not answer on $URL (set BACKEND_URL=...)"
[ $UNDO = 1 ] && undo

ROWS=$(sql -N -e "SELECT COUNT(*) FROM replay_metadata")
FREE_GB=$(df -BG --output=avail "$STATE" | tail -1 | tr -dc 0-9)
AVAIL_MB=$(free -m | awk '/^Mem:/{print $7}')
say "Replays: $ROWS   Free disk: ${FREE_GB} GB   Free memory: ${AVAIL_MB} MB"
[ "$FREE_GB" -ge 3 ] || die "less than 3 GB of free disk"

# The code must be the version this upgrade was made from: a file changed since would be overwritten.
if ! is_done code; then
  changed=""
  while read -r sum f; do
    [ -e "$APP/$f" ] || { changed+=" $f(missing)"; continue; }
    [ "$(sha256sum "$APP/$f" | cut -d' ' -f1)" = "$sum" ] || changed+=" $f"
  done < "$HERE/original.sha256"
  if [ -n "$changed" ]; then
    warn "These files differ from the version this upgrade was made from:$changed"
    warn "The upgrade would replace them. Send them over first if they have changes worth keeping."
    [ $ALL = 1 ] && die "files changed since the upgrade was prepared (run without --all to decide)"
    read -r -p "   Replace them anyway? [y/N] " a </dev/tty; [[ "$a" =~ ^[Yy] ]] || die "kept your files"
  fi
fi

# --- 1. backup ------------------------------------------------------------------------------
if ! is_done backup; then
  ( cd "$APP" && tar -czf "$STATE/backup/code.tar.gz" backend/app.py backend/models.py frontend/alt_front.py ) || die "code backup failed"
  cp -a "$CNF_DIR" "$STATE/backup/mariadb.conf.d" 2>/dev/null
  $SYSTEMCTL cat "$BACKEND_UNIT" "$FRONTEND_UNIT" > "$STATE/backup/units.txt" 2>/dev/null
  done_ backup; ok "backup of the code and settings in $STATE/backup (small)"
fi
if ! is_done dump && ask "Back up the database first (recommended)" \
    "A copy of the whole replay table to put back if anything goes wrong. Uploads and searches keep working while it runs." \
    "About a minute and ~100 MB of disk in $STATE/backup."; then
  t=$SECONDS
  set -o pipefail
  $DUMP --single-transaction --quick "$DB" replay_metadata | gzip -1 > "$STATE/backup/replay_metadata.sql.gz" || die "database backup failed"
  done_ dump; ok "database backup $(du -h "$STATE/backup/replay_metadata.sql.gz" | cut -f1) in $(secs $((SECONDS - t)))"
fi

# --- 2. database cache ------------------------------------------------------------------------
if ! is_done cache; then
  CUR=$(sql -N -e "SELECT @@innodb_buffer_pool_size DIV 1048576")
  if [ "$CUR" -ge 1024 ]; then done_ cache; ok "database cache already ${CUR} MB"
  elif [ "$AVAIL_MB" -lt 1400 ]; then warn "only ${AVAIL_MB} MB of free memory: leaving the database cache at ${CUR} MB"
  elif ask "Database cache: ${CUR} MB -> 1024 MB" \
      "The replay table and its indexes are ~920 MB, so with ${CUR} MB most searches read from disk. Measured on a copy of this database while 4,000 uploads came in: a search took 20.5 s with ${CUR} MB and 3.9 s with 1 GB; searches that failed: 93 -> 5; the 4,000 uploads finished in 188 s instead of 812 s. No extra cost: memory the server already has (${AVAIL_MB} MB free now)." \
      "MariaDB restarts: about 10 seconds where uploads and searches fail (the web services restart right after)."; then
    printf '# replay server upgrade: keep the replay table and its indexes in memory\n[mysqld]\ninnodb_buffer_pool_size = 1G\n' > "$CNF_DIR/99-replay-server.cnf"
    $SYSTEMCTL restart mariadb || { rm -f "$CNF_DIR/99-replay-server.cnf"; $SYSTEMCTL restart mariadb; die "MariaDB did not start with 1 GB; setting removed and restarted as before"; }
    for i in $(seq 1 30); do sql -N -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
    restart_web
    done_ cache; ok "database cache $(sql -N -e "SELECT @@innodb_buffer_pool_size DIV 1048576") MB"
  fi
fi

# --- 3. rank columns and summary tables -------------------------------------------------------
if ! is_done schema; then
  if sql -N -e "SHOW COLUMNS FROM replay_metadata LIKE 'p1_level'" | grep -q p1_level && sql -N -e "SHOW TABLES LIKE 'player_characters'" | grep -q .; then
    done_ schema; ok "rank columns already there"
  elif ask "Rank columns and summary tables (needed by everything below)" \
      "Two columns for each player's rank, and three small empty tables (players, their names, their characters) that uploads keep up to date. The indexes that make searches fast come in step 7, once the ranks are filled." \
      "Instant (no table rebuild): uploads and searches keep working."; then
    t=$SECONDS; n=0
    until { echo "SET SESSION lock_wait_timeout = 10;"; cat "$HERE/sql/001a_columns.sql"; } | sql; do
      n=$((n + 1)); [ $n -ge 5 ] && die "the table stayed busy; run the script again later"
      warn "the table was busy; trying again in 20 s ($n/5)"; sleep 20
    done
    done_ schema; ok "rank columns and tables added in $(secs $((SECONDS - t)))"
  fi
fi

# --- 4. code ----------------------------------------------------------------------------------
if ! is_done code; then
  if ! is_done schema; then warn "skipping the new code: it needs the rank columns (step 3)"
  elif ask "New code (backend and website)" \
      "The new search API (/api/v2/...) and statistics; uploads also store the ranks and update the summaries (from now on, so no upload is missed while the older ranks are read). Two bug fixes: the website's name box could run SQL typed into it (SQL injection), and a failed query left its database connection open (it can block every later query). Everything that works today answers exactly the same (tested route by route)." \
      "Both web services restart: a few seconds without answers. The new /api/v2 searches answer once step 7 has built their indexes (the mod falls back to the old routes until then)."; then
    cp "$HERE/files/backend/"*.py "$APP/backend/" && cp "$HERE/files/frontend/alt_front.py" "$APP/frontend/" || die "could not copy the files"
    restart_web
    done_ code; ok "new code running"
  fi
fi
if ! is_done threads && is_done code; then
  if ask "Backend: 8 threads per worker" \
      "Today the backend has 2 workers, so two slow searches stop every upload and download until they finish. With 8 threads each, the others keep going. The 4,000-upload test above ran this way." \
      "The backend restarts: a few seconds without answers. Memory: a few MB."; then
    EXEC=$($SYSTEMCTL cat "$BACKEND_UNIT" | grep '^ExecStart=' | tail -1 | cut -d= -f2-)
    if [ -z "$EXEC" ] || ! echo "$EXEC" | grep -q gunicorn; then warn "could not read the backend's start command; left as it is"
    else
      echo "$EXEC" | grep -q -- '--threads' && NEWEXEC="$EXEC" || NEWEXEC="${EXEC/gunicorn /gunicorn --threads 8 }"
      mkdir -p "$UNIT_DIR/$BACKEND_UNIT.service.d"
      printf '# replay server upgrade\n[Service]\nExecStart=\nExecStart=%s\n' "$NEWEXEC" > "$UNIT_DIR/$BACKEND_UNIT.service.d/replay-upgrade.conf"
      $SYSTEMCTL daemon-reload; restart_web
      done_ threads; ok "backend: $NEWEXEC"
    fi
  fi
fi

# --- 6. ranks of the replays already stored --------------------------------------------------
if ! is_done levels && is_done schema; then
  if is_done indexes; then warn "the search indexes are already built: reading the ranks now updates each index row by row (slow)"; fi
  if ask "Read the ranks of the replays already stored" \
      "The ranks are in the replay files, not in the table: this reads both players' rank from each of the $ROWS files (only the first bytes of each) and stores them. New uploads get theirs automatically." \
      "Reads $ROWS files at the lowest disk and CPU priority (uploads and searches go first); about 10-30 minutes, shown as it goes. Then the table is updated 20,000 rows at a time."; then
    t=$SECONDS
    if [ -n "${LEVELS_SQL:-}" ]; then cp "$LEVELS_SQL" "$STATE/levels.sql"   # test runs: ranks already read
    else nice -n 19 ionice -c3 $PY "$HERE/tools/read_levels.py" "$REPLAYS" > "$STATE/levels.sql" || die "reading the replay files failed"
    fi
    ok "ranks read in $(secs $((SECONDS - t)))"
    t=$SECONDS
    { echo "DROP TABLE IF EXISTS levels_import; CREATE TABLE levels_import (filename VARCHAR(255) NOT NULL PRIMARY KEY, l1 SMALLINT NULL, l2 SMALLINT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;"
      cat "$STATE/levels.sql"; } | sql || die "loading the ranks into a work table failed"
    BOUNDS=$(sql -N -e "SELECT filename FROM (SELECT filename, ROW_NUMBER() OVER (ORDER BY filename) rn FROM replay_metadata) t WHERE rn % 20000 = 1 ORDER BY filename")
    mapfile -t BS <<< "$BOUNDS"
    for ((i = 0; i < ${#BS[@]}; i++)); do
      lo="${BS[$i]//\'/\'\'}"; hi="${BS[$((i + 1))]:-}"; hi="${hi//\'/\'\'}"
      cond="r.filename >= '$lo'"; [ -n "$hi" ] && cond+=" AND r.filename < '$hi'"
      sql -e "UPDATE replay_metadata r JOIN levels_import l ON l.filename = r.filename SET r.p1_level = l.l1, r.p2_level = l.l2 WHERE $cond" \
        || die "updating ranks failed at batch $i (run the script again: it starts this step over)"
      [ $((i % 10)) = 0 ] && echo "   $((i * 20000)) / $ROWS rows"
    done
    sql -e "DROP TABLE levels_import"
    done_ levels; ok "ranks stored in $(secs $((SECONDS - t))): $(sql -N -e "SELECT SUM(p1_level IS NOT NULL) + SUM(p2_level IS NOT NULL) FROM replay_metadata") player ranks"
  fi
fi

# --- 7. indexes -------------------------------------------------------------------------------
if ! is_done indexes && is_done schema; then
  if sql -N -e "SHOW INDEX FROM replay_metadata WHERE Key_name = 'idx_p1_player'" | grep -q .; then done_ indexes; ok "indexes already there"
  elif ask "Indexes shaped for each kind of search" \
      "Measured on a copy of this database: a character's replays page 20: 114 s -> 0.7 s; a player's replays on one character: 4.4 s -> 0.07 s; replays per character of a player: 12.6 s -> 0.03 s; name search: 7.2 s -> 0.14 s. Six old indexes are replaced by wider ones that start with the same columns, so nothing that used them gets slower." \
      "Online: uploads and searches keep working while they build. About 3 minutes, ~+60 MB of disk. If something is holding the table it waits 10 s, gives up and tries again (5 times)."; then
    is_done levels || warn "the ranks are not read yet: building the indexes now makes reading them very slow later"
    t=$SECONDS; n=0
    until { echo "SET SESSION lock_wait_timeout = 10;"; cat "$HERE/sql/001b_indexes.sql"; } | sql; do
      n=$((n + 1)); [ $n -ge 5 ] && die "the table stayed busy; run the script again later"
      warn "the table was busy; trying again in 20 s ($n/5)"; sleep 20
    done
    done_ indexes; ok "indexes built in $(secs $((SECONDS - t)))"
  fi
fi

# --- 8. summaries -----------------------------------------------------------------------------
if ! is_done summaries && is_done schema; then
  if ! is_done levels; then warn "skipping the summaries until the ranks are read (step 6)"
  elif ask "Work out the player and statistics summaries" \
      "One row per player, per player and character, per matchup, per month: what the player pages, leaderboards and statistics read, so they never scan the replay table (statistics overview: not possible today -> 3 ms). Uploads keep them up to date from now on." \
      "Reads the replay table a few times: a few minutes, uploads and searches keep working."; then
    t=$SECONDS
    for f in 002_fill_summaries.sql 003_level_summaries.sql 004_stats.sql 005_uploads.sql; do
      sql < "$HERE/sql/$f" || die "$f failed (run the script again: it starts this step over)"
      ok "$f"
    done
    done_ summaries; ok "summaries in $(secs $((SECONDS - t)))"
  fi
fi

# --- 9. check ---------------------------------------------------------------------------------
echo; echo "${B}== Check${N}"
check() {
  local out code time
  out=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' "$URL$1"); code=${out% *}; time=${out#* }
  if [ "$code" = 200 ]; then echo "   ${G}ok${N}  ${time}s  $1"; else echo "   ${R}$code${N}  $1"; FAILED=1; fi
}
FAILED=0
check "/get_replays?page_size=1"
if is_done code; then
  check "/api/v2/players?q=ra"
  is_done indexes && check "/api/v2/replays?character=1&limit=5"
  is_done summaries && { check "/api/v2/stats/overview"; check "/api/v2/directory?limit=5"; }
fi
echo
if [ $FAILED = 0 ]; then echo "${G}${B}Finished.${N} Log: $LOG"
else echo "${R}${B}Some checks failed${N}: journalctl -u $BACKEND_UNIT -n 50. To go back: sudo bash $0 --undo"; fi
skipped=""; for s in cache schema code threads levels indexes summaries; do is_done $s || skipped+=" $s"; done
[ -n "$skipped" ] && echo "Not done (run the script again to be asked again):$skipped"
exit 0
