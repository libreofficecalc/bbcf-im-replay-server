-- Fills the summary tables from the replays already stored (once, after 001; uploads keep them
-- up to date afterwards). Reads the table once per statement; a few seconds to a minute.
-- Levels are filled separately (003), from the replay files.

INSERT INTO player_names (steamid64, name, replays, last_used)
SELECT sid, LEFT(nm, 64), COUNT(*), MAX(dt) FROM (
  SELECT p1_steamid64 AS sid, p1 AS nm, datetime_ AS dt FROM replay_metadata WHERE p1_steamid64 > 0
  UNION ALL
  SELECT p2_steamid64, p2, datetime_ FROM replay_metadata WHERE p2_steamid64 > 0
) s WHERE nm IS NOT NULL GROUP BY sid, LEFT(nm, 64)
ON DUPLICATE KEY UPDATE replays = VALUES(replays), last_used = VALUES(last_used);

INSERT INTO players (steamid64, name, replays, last_played)
SELECT n.steamid64,
       (SELECT n2.name FROM player_names n2 WHERE n2.steamid64 = n.steamid64 ORDER BY n2.last_used DESC LIMIT 1),
       SUM(n.replays), MAX(n.last_used)
FROM player_names n GROUP BY n.steamid64
ON DUPLICATE KEY UPDATE name = VALUES(name), replays = VALUES(replays), last_played = VALUES(last_played);

INSERT INTO player_characters (steamid64, toon, replays, wins, max_level, last_level, last_played)
SELECT sid, toon, COUNT(*), SUM(won), MAX(lvl), NULL, MAX(dt) FROM (
  SELECT p1_steamid64 AS sid, p1_toon AS toon, (winner = 0) AS won, p1_level AS lvl, datetime_ AS dt FROM replay_metadata WHERE p1_steamid64 > 0
  UNION ALL
  SELECT p2_steamid64, p2_toon, (winner = 1), p2_level, datetime_ FROM replay_metadata WHERE p2_steamid64 > 0
) s GROUP BY sid, toon
ON DUPLICATE KEY UPDATE replays = VALUES(replays), wins = VALUES(wins), max_level = VALUES(max_level), last_played = VALUES(last_played);
