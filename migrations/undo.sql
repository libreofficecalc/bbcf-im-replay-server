-- Puts the database back as it was before the upgrade: the summary tables go, replay_metadata
-- loses the two rank columns and the new indexes and gets its old indexes back. Online (InnoDB
-- rebuilds the table while reads and uploads keep working). Only with the old code running.
DROP TABLE IF EXISTS levels_import, player_matchups, stats_cache, activity_months, activity_days,
  player_characters, player_names, players;

ALTER TABLE replay_metadata
  DROP KEY IF EXISTS idx_p1_player, DROP KEY IF EXISTS idx_p2_player,
  DROP KEY IF EXISTS idx_p1_steam_date, DROP KEY IF EXISTS idx_p2_steam_date,
  DROP KEY IF EXISTS idx_p1_toon_date, DROP KEY IF EXISTS idx_p2_toon_date,
  DROP KEY IF EXISTS idx_matchup, DROP KEY IF EXISTS idx_levels,
  DROP KEY IF EXISTS idx_p1_toon_upload, DROP KEY IF EXISTS idx_p2_toon_upload,
  DROP COLUMN IF EXISTS p1_level, DROP COLUMN IF EXISTS p2_level,
  ADD KEY IF NOT EXISTS idx_p1_toon (p1_toon),
  ADD KEY IF NOT EXISTS idx_p2_toon (p2_toon),
  ADD KEY IF NOT EXISTS idx_p1_steamid64 (p1_steamid64),
  ADD KEY IF NOT EXISTS idx_p2_steamid64 (p2_steamid64),
  ADD KEY IF NOT EXISTS idx_p1_toon_sort (p1_toon, upload_datetime_, datetime_),
  ADD KEY IF NOT EXISTS idx_p2_toon_sort (p2_toon, upload_datetime_, datetime_),
  ALGORITHM=INPLACE, LOCK=NONE;
