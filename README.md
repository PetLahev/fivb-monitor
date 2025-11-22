# FIVB Withdrawn Teams Monitor

This project automatically monitors upcoming FIVB beach‑volleyball
tournaments, tracks team withdrawals, stores historical snapshots, and
exposes the data via a web UI and JSON API.

## How it Works

### 1. FIVB VIS SDK

The scraper queries the official **FIVB VIS XML API**: -
`GetEventList` - `GetEvent` - `GetBeachTeamList`

It fetches all upcoming tournaments (rolling 28‑day window), normalizes
team data, and stores snapshots in PostgreSQL.

### 2. Withdrawal Detection Logic

For each run: - Teams are loaded into `tournament_team_snapshot` with a
timestamp (`run_date`). - If a team was present in the *previous* run
but does **not** appear in the new run, the system marks: -
**withdrawn_at = date of first disappearance**

This produces reliable daily withdrawal tracking.

### 3. Cron Automation

A cron job runs the scraper **3× daily**:

    python fivb_scraper.py

The year parameter is no longer needed, because the scraper always loads
upcoming events.

### 4. Hosting (Hetzner)

The system is hosted on a **Hetzner cloud server** (SSH accessible).\
The app runs via systemd service + uvicorn + FastAPI.

### 5. Logging Into the Server

SSH login uses your private key:

    ssh -i ~/.ssh/hetzner_ed25519 app@91.99.24.177

Example WinSCP setup: - Protocol: **SFTP** - Host: server IP - Username:
`app` - Authentication: **select private key** - Convert key to PuTTY
format when prompted.

### 6. Database Access

The server uses PostgreSQL.\
Login example:

    psql postgresql://postgres:<PASSWORD>@localhost:5432/fivb_monitor

From VS Code / DBeaver, use: - Host: `127.0.0.1` - User: `postgres` -
DB: `fivb_monitor`

### 7. Updating the Code on the Server

    ssh app@<SERVER-IP>
    cd ~/fivb-monitor
    source .venv/bin/activate
    git pull
    sudo systemctl restart fivb-monitor

This reloads the application with the latest code.

### 8. API Description

All API routes are under `/api`.

#### **GET `/api/withdrawals/{tournament_fivb_no}`**

Returns all teams that withdrew from a specific tournament.

Response example:

``` json
[
  {
    "team_id": 1234,
    "display_name": "Perusic/Schweiner",
    "player1_name": "Ondrej Perusic",
    "player2_name": "David Schweiner",
    "fivb_player1_no": 56789,
    "fivb_player2_no": 67890,
    "country_code": "CZE",
    "withdrawn_at": "2025-03-12",
    "tournament_id": 44,
    "tournament_fivb_no": 3152,
    "event_id": 101,
    "event_fivb_no": 8899
  }
]
```

Notes: - All **IDs are real FIVB IDs where possible** (team/player
numbers). - Withdrawn date is computed using the snapshot comparison
logic described above.

### 9. Project Structure

-   `fivb_scraper.py` --- collects data from VIS API\
-   `db_store.py` --- PostgreSQL persistence layer\
-   `main.py` --- FastAPI app + HTML UI\
-   `api.py` --- JSON API endpoints\
-   `templates/` --- Jinja templates\
-   `static/` --- CSS and country flags\
-   `cronjob` --- scheduled scraper execution

### 10. Country Flags

Flags use local SVG or PNG files stored in:

    /static/flags/ISO3.svg

Displayed as:

    CZE  🇨🇿

------------------------------------------------------------------------

## Summary

This service automates FIVB beach volleyball withdrawal‑tracking with: -
Reliable snapshot‑based change detection\
- Clean JSON API\
- Lightweight UI\
- Full automation on Hetzner via cron and systemd

If you need to extend the API or data model later, the project is ready
for modular expansion.
