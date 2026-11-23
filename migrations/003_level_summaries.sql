-- After the levels are filled (deploy/read_levels.py, step 6 of upgrade.sh): each player's highest and latest level on
-- each character. Uploads keep them up to date afterwards.
UPDATE player_characters pc JOIN (
  SELECT sid, toon, MAX(lvl) AS mx,
         SUBSTRING_INDEX(GROUP_CONCAT(lvl ORDER BY dt DESC), ',', 1) AS latest
  FROM (
    SELECT p1_steamid64 AS sid, p1_toon AS toon, p1_level AS lvl, datetime_ AS dt FROM replay_metadata WHERE p1_level IS NOT NULL AND p1_steamid64 > 0
    UNION ALL
    SELECT p2_steamid64, p2_toon, p2_level, datetime_ FROM replay_metadata WHERE p2_level IS NOT NULL AND p2_steamid64 > 0
  ) s GROUP BY sid, toon
) l ON l.sid = pc.steamid64 AND l.toon = pc.toon
SET pc.max_level = l.mx, pc.last_level = l.latest;
