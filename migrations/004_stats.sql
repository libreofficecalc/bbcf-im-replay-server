-- Statistics: per player matchup records, a cache for database-wide numbers, room for a country.
-- Run after 001-003. Online and short: the new tables are small (one row per player, character
-- and opponent character); the fill reads the replays once.

-- Per account, character and opponent character: games and wins. Matchup win rates, "best Ragna
-- against Kokonoe" leaderboards and the database-wide matchup table all read this, never the
-- replays. Uploads add one game to two rows (one per side).
CREATE TABLE IF NOT EXISTS player_matchups (
  steamid64 BIGINT NOT NULL,
  toon TINYINT NOT NULL,
  opp_toon TINYINT NOT NULL,
  games INT NOT NULL DEFAULT 0,
  wins INT NOT NULL DEFAULT 0,
  ranked_games INT NOT NULL DEFAULT 0,   -- both sides had a rank: a ranked match, not a player match
  ranked_wins INT NOT NULL DEFAULT 0,
  last_played DATETIME NULL,
  PRIMARY KEY (steamid64, toon, opp_toon),
  KEY idx_matchup_games (toon, opp_toon, games)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

INSERT INTO player_matchups (steamid64, toon, opp_toon, games, wins, ranked_games, ranked_wins, last_played)
SELECT sid, toon, opp, COUNT(*), SUM(won), SUM(ranked), SUM(won * ranked), MAX(dt) FROM (
  SELECT p1_steamid64 AS sid, p1_toon AS toon, p2_toon AS opp, IF(winner = 0, 1, 0) AS won,
         IF(p1_level IS NOT NULL AND p2_level IS NOT NULL, 1, 0) AS ranked, datetime_ AS dt
    FROM replay_metadata WHERE p1_steamid64 > 0
  UNION ALL
  SELECT p2_steamid64, p2_toon, p1_toon, IF(winner = 1, 1, 0),
         IF(p1_level IS NOT NULL AND p2_level IS NOT NULL, 1, 0), datetime_
    FROM replay_metadata WHERE p2_steamid64 > 0
) s GROUP BY sid, toon, opp
ON DUPLICATE KEY UPDATE games = VALUES(games), wins = VALUES(wins), ranked_games = VALUES(ranked_games),
  ranked_wins = VALUES(ranked_wins), last_played = VALUES(last_played);

-- Database-wide numbers (totals, per character, matchup table, ranks, activity), worked out at
-- most once an hour by the first request that finds them stale and read by everyone else.
CREATE TABLE IF NOT EXISTS stats_cache (
  name VARCHAR(32) NOT NULL PRIMARY KEY,
  made DATETIME NOT NULL,
  body MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Room for a player's country (ISO 3166 two letters), not filled yet.
ALTER TABLE players ADD COLUMN IF NOT EXISTS country CHAR(2) NULL;

-- Activity counters, so that no statistic ever counts the replays table: matches per month (by
-- match date; their sum is the number of replays) and uploads per day. Uploads add one to each.
CREATE TABLE IF NOT EXISTS activity_months (
  month CHAR(7) NOT NULL PRIMARY KEY,
  matches INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS activity_days (
  day DATE NOT NULL PRIMARY KEY,
  uploads INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO activity_months (month, matches)
SELECT DATE_FORMAT(datetime_, '%Y-%m') m, COUNT(*) FROM replay_metadata WHERE datetime_ IS NOT NULL GROUP BY m
ON DUPLICATE KEY UPDATE matches = VALUES(matches);
INSERT INTO activity_days (day, uploads)
SELECT DATE(upload_datetime_) d, COUNT(*) FROM replay_metadata WHERE upload_datetime_ IS NOT NULL GROUP BY d
ON DUPLICATE KEY UPDATE uploads = VALUES(uploads);
