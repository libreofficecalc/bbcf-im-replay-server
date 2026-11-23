-- Upgrade part 2 of 2: indexes shaped for each kind of search (each line says what it is
-- for). Online: InnoDB builds them while reads and uploads keep working. Run after the ranks
-- are filled, so each index is built once, in order.
ALTER TABLE replay_metadata
  ADD KEY IF NOT EXISTS idx_p1_player (p1_steamid64, p1_toon, datetime_, filename, p2_toon, p1_level, p2_level, winner),
  ADD KEY IF NOT EXISTS idx_p2_player (p2_steamid64, p2_toon, datetime_, filename, p1_toon, p1_level, p2_level, winner),
  ADD KEY IF NOT EXISTS idx_p1_steam_date (p1_steamid64, datetime_, filename, p2_toon, p1_level, p2_level, winner, p1_toon, upload_datetime_),   -- a player's replays, any character, newest first
  ADD KEY IF NOT EXISTS idx_p2_steam_date (p2_steamid64, datetime_, filename, p1_toon, p1_level, p2_level, winner, p2_toon, upload_datetime_),
  ADD KEY IF NOT EXISTS idx_p1_toon_date (p1_toon, datetime_, filename, p2_toon, p1_level, p2_level, winner),
  ADD KEY IF NOT EXISTS idx_p2_toon_date (p2_toon, datetime_, filename, p1_toon, p1_level, p2_level, winner),
  ADD KEY IF NOT EXISTS idx_matchup (p1_toon, p2_toon, datetime_, filename, p1_level, p2_level, winner),   -- one character against another, either way round
  ADD KEY IF NOT EXISTS idx_levels (p1_level, p2_level),
  ADD KEY IF NOT EXISTS idx_p1_toon_upload (p1_toon, upload_datetime_, filename, p2_toon, p1_level, p2_level, winner, datetime_),   -- "newest upload first" for a character
  ADD KEY IF NOT EXISTS idx_p2_toon_upload (p2_toon, upload_datetime_, filename, p1_toon, p1_level, p2_level, winner, datetime_),
  DROP KEY IF EXISTS idx_p1_toon_sort,   -- (toon, upload, date): no query can walk it in order (the old page ORs both sides)
  DROP KEY IF EXISTS idx_p2_toon_sort,
  DROP KEY IF EXISTS idx_p1_steamid64,   -- the first column of idx_p1_player
  DROP KEY IF EXISTS idx_p2_steamid64,
  DROP KEY IF EXISTS idx_p1_toon,        -- the first column of idx_p1_toon_date
  DROP KEY IF EXISTS idx_p2_toon,
  ALGORITHM=INPLACE, LOCK=NONE;
