-- How many replays each player uploaded themselves (they were the recorder: recorder_steamid64),
-- next to how many they are in (players.replays). Run after 004. Online and short: one pass over
-- the replays' recorder column; uploads keep it current (api_v2 record_summaries).
ALTER TABLE players ADD COLUMN IF NOT EXISTS uploads INT NOT NULL DEFAULT 0;

UPDATE players p
JOIN (SELECT recorder_steamid64 AS steamid64, COUNT(*) AS n FROM replay_metadata
      WHERE recorder_steamid64 IS NOT NULL GROUP BY recorder_steamid64) u ON u.steamid64 = p.steamid64
SET p.uploads = u.n;
