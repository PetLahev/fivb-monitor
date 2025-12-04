-- =========================
-- FIVB Monitor – DB schema
-- =========================

SET client_min_messages = WARNING;

-- Tables without dependencies
CREATE TABLE IF NOT EXISTS event (
  event_id SERIAL PRIMARY KEY,
  fivb_event_no INTEGER UNIQUE NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS player (
  player_id SERIAL PRIMARY KEY,
  fivb_player_no INTEGER UNIQUE,
  name TEXT
);

CREATE TABLE IF NOT EXISTS team (
  team_id SERIAL PRIMARY KEY,
  player1_id INTEGER NOT NULL REFERENCES player(player_id),
  player2_id INTEGER NOT NULL REFERENCES player(player_id),
  display_name TEXT,
  country_code  varchar(3),
  CONSTRAINT team_players_order CHECK (player1_id < player2_id),
  CONSTRAINT team_players_unique UNIQUE (player1_id, player2_id),
  CONSTRAINT team_country_code_chk CHECK (country_code IS NULL OR country_code ~ '^[A-Z]{3}$')
);

CREATE TABLE IF NOT EXISTS crawl_run (
  run_id SERIAL PRIMARY KEY,
  run_date DATE NOT NULL UNIQUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  note TEXT
);

-- Event's tournaments
CREATE TABLE IF NOT EXISTS tournament (
  tournament_id SERIAL PRIMARY KEY,
  fivb_tournament_no INTEGER UNIQUE NOT NULL,
  event_id INTEGER NOT NULL REFERENCES event(event_id) ON DELETE CASCADE,
  gender TEXT CHECK (gender IN ('M','W')),
  CONSTRAINT ux_tournament_event_gender UNIQUE (event_id, gender)
);

-- Status type
DO $$
BEGIN
  BEGIN
    CREATE TYPE team_status AS ENUM ('Registered', 'Withdrawn', 'WithdrawnWithMedicalCert', 'Deleted');
  EXCEPTION WHEN duplicate_object THEN
    NULL; -- already exists
  END;
END$$;

-- Snapshoty (depends on tournament/team/crawl_run + team_status)
CREATE TABLE IF NOT EXISTS tournament_team_snapshot (
  tournament_id INTEGER NOT NULL REFERENCES tournament(tournament_id) ON DELETE CASCADE,
  team_id INTEGER NOT NULL REFERENCES team(team_id),
  run_id INTEGER NOT NULL REFERENCES crawl_run(run_id) ON DELETE CASCADE,
  status team_status NOT NULL,
  rank INTEGER,
  PRIMARY KEY (tournament_id, team_id, run_id)
);

-- Index
CREATE INDEX IF NOT EXISTS idx_event_dates        ON event(start_date DESC, end_date DESC);
CREATE INDEX IF NOT EXISTS idx_tournament_event   ON tournament(event_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_tourn_run ON tournament_team_snapshot (tournament_id, run_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_team      ON tournament_team_snapshot (team_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_status    ON tournament_team_snapshot (status);

-- View for the first day when a team withdrew from a tournament
CREATE OR REPLACE VIEW v_tournament_team_withdrawal AS
WITH ordered AS (
  SELECT
    tts.tournament_id,
    tts.team_id,
    cr.run_date,
    tts.status,
    LAG(tts.status) OVER (PARTITION BY tts.tournament_id, tts.team_id ORDER BY cr.run_date) AS prev_status
  FROM tournament_team_snapshot tts
  JOIN crawl_run cr ON cr.run_id = tts.run_id
)
SELECT
  tournament_id,
  team_id,
  MIN(run_date) AS withdrawn_at
FROM ordered
WHERE status = 'Withdrawn'
  AND (prev_status IS DISTINCT FROM 'Withdrawn')
GROUP BY tournament_id, team_id;
SQL