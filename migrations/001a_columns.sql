-- Upgrade part 1 of 2: the two rank columns (instant: no table rebuild) and the small summary
-- tables. The indexes come later (001b_indexes.sql), once the ranks are filled: filling them
-- while ten indexes carry the ranks would update every index row by row (days instead of minutes).
ALTER TABLE replay_metadata
  ADD COLUMN IF NOT EXISTS p1_level SMALLINT NULL,
  ADD COLUMN IF NOT EXISTS p2_level SMALLINT NULL,
  ALGORITHM=INSTANT;


-- One row per Steam account: its latest name, replays, last match.
CREATE TABLE IF NOT EXISTS players (
  steamid64 BIGINT NOT NULL PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  replays INT NOT NULL DEFAULT 0,
  last_played DATETIME NULL,
  KEY idx_replays (replays)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

-- Every name an account has used (name search looks here).
CREATE TABLE IF NOT EXISTS player_names (
  steamid64 BIGINT NOT NULL,
  name VARCHAR(64) NOT NULL,
  replays INT NOT NULL DEFAULT 0,
  last_used DATETIME NULL,
  PRIMARY KEY (steamid64, name),
  KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

-- Per account and character: replays, wins, levels, last match.
CREATE TABLE IF NOT EXISTS player_characters (
  steamid64 BIGINT NOT NULL,
  toon TINYINT NOT NULL,
  replays INT NOT NULL DEFAULT 0,
  wins INT NOT NULL DEFAULT 0,
  max_level SMALLINT NULL,
  last_level SMALLINT NULL,
  last_played DATETIME NULL,
  PRIMARY KEY (steamid64, toon),
  KEY idx_toon_replays (toon, replays),
  KEY idx_toon_level (toon, last_level),
  KEY idx_toon_recent (toon, last_played)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
