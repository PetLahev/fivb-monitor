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
http://91.99.24.177/
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

    psql "postgresql://fivb_user:<PASSWORD>@localhost:5432/fivb_monitor"

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

The project exposes a small JSON API that is intended to be consumed by other services
(e.g. a tournament front-end like `fivb.12ndr.at`).

All responses are UTF-8 JSON and use ISO date strings (`YYYY-MM-DD`) where applicable.

#### 1. Withdrawn teams by FIVB tournament No

`GET /api/tournament/{fivb_no}/withdrawals`

Example:
```http
GET /api/tournament/8136/withdrawals
```
Response example:
``` json
[
  {
    "team_id": 123,
    "display_name": "Perusic/Schweiner",
    "player1_name": "Ondrej Perusic",
    "player2_name": "David Schweiner",
    "fivb_player1_no": 11111,
    "fivb_player2_no": 22222,
    "country_code": "CZE",
    "withdrawn_at": "2025-11-18",
    "tournament_id": 10,
    "tournament_fivb_no": 8136,
    "event_id": 5,
    "event_fivb_no": 1559,
    "last_checked": "2025-11-19"
  }
]
```
withdrawn_at is the first date on which a team disappears from the VIS team list
compared to the previous crawl run.
In other words:
previous day: team was still present
today: team is missing ⇒ withdrawn_at = today
last_checked is the last crawl date for this tournament (max crawl_run.run_date)

#### 2. Withdrawn teams by TCODE (MITA2025 / WITA2025)

Some clients (e.g. fivb.12ndr.at) identify tournaments using a short TCODE such as:
MITA2025 – Men’s event Itapema 2025
WITA2025 – Women’s event Itapema 2025

The format is:
-   first character: M or W (gender)
-   next three characters: event short code, e.g. ITA
-   remaining characters: year, e.g. 2025

Internally this is mapped to our event code:
BVB-{EVENT}{YEAR}
e.g. BVB-ITA2025

Endpoint:
```
GET /api/tcode/{tcode}/withdrawals
```

Examples:
```GET /api/tcode/MITA2025/withdrawals```
```GET /api/tcode/WITA2025/withdrawals```

The JSON response has exactly the same shape as the tournament/{fivb_no}/withdrawals
endpoint, including:
-   real FIVB player numbers (fivb_player1_no, fivb_player2_no)
-   internal IDs (team_id, tournament_id, event_id)
-   FIVB event and tournament numbers (event_fivb_no, tournament_fivb_no)
-   withdrawn_at (first disappearance date based on crawl snapshots)
-   last_checked (last crawl date for this tournament)

Error codes:
-   400 – invalid TCODE format (wrong length, missing gender prefix, etc.)
-   404 – event not found for this TCODE, or tournament for the requested gender not found

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
