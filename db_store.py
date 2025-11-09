# db_store.py
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

# --------- Connection config ---------
@dataclass
class DBConfig:
    # preferuje DATABASE_URL, jinak jednotlivé proměnné
    database_url: Optional[str] = os.getenv("DATABASE_URL")
    host: str = os.getenv("PGHOST", "localhost")
    port: int = int(os.getenv("PGPORT", "5432"))
    dbname: str = os.getenv("PGDATABASE", "fivb_monitor")
    user: str = os.getenv("PGUSER", "postgres")
    password: str = os.getenv("PGPASSWORD", "postgres")

    def connect(self):
        if self.database_url:
            return psycopg2.connect(self.database_url, cursor_factory=psycopg2.extras.DictCursor)
        return psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            cursor_factory=psycopg2.extras.DictCursor,
        )

# --------- Public model types (kompatibilní s fivb_scraper.py) ---------
@dataclass(frozen=True)
class Event:
    no: int
    code: str
    name: str
    start_date: date
    end_date: date

@dataclass(frozen=True)
class BeachTournamentRef:
    tournament_no: int
    gender: Optional[str]  # 'M'/'W' or None

@dataclass(frozen=True)
class BeachTeam:
    no_player1: Optional[int]
    no_player2: Optional[int]
    name: str
    rank: Optional[int]
    status: str  # 'Registered' | 'Withdrawn'

@dataclass
class EventTeamsSnapshot:
    event: Event
    tournaments: List[BeachTournamentRef]
    teams_by_tournament: Dict[int, List[BeachTeam]]

# --------- Storage service ---------
class Storage:
    def __init__(self, cfg: DBConfig | None = None):
        self.cfg = cfg or DBConfig()

    # ---- upsert helpers ----
    def upsert_event(self, cur, ev: Event) -> int:
        cur.execute(
            """
            INSERT INTO event (fivb_event_no, code, name, start_date, end_date)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fivb_event_no) DO UPDATE
              SET code = EXCLUDED.code,
                  name = EXCLUDED.name,
                  start_date = EXCLUDED.start_date,
                  end_date = EXCLUDED.end_date
            RETURNING event_id;
            """,
            (ev.no, ev.code, ev.name, ev.start_date, ev.end_date),
        )
        return cur.fetchone()["event_id"]

    def upsert_tournament(self, cur, event_id: int, tref: BeachTournamentRef) -> int:
        gender = tref.gender if tref.gender in ("M", "W") else None
        cur.execute(
            """
            INSERT INTO tournament (fivb_tournament_no, event_id, gender)
            VALUES (%s, %s, %s)
            ON CONFLICT (fivb_tournament_no) DO UPDATE
              SET event_id = EXCLUDED.event_id,
                  gender   = COALESCE(EXCLUDED.gender, tournament.gender)
            RETURNING tournament_id;
            """,
            (tref.tournament_no, event_id, gender),
        )
        return cur.fetchone()["tournament_id"]

    def upsert_player(self, cur, fivb_no: Optional[int], name: Optional[str]) -> int:
        """
        Preferujeme identifikaci hráče přes FIVB číslo (NoPlayerX).
        Pokud číslo není, zkusíme najít podle jména (bez unique constraintu; je to best-effort).
        """
        if fivb_no is not None:
            cur.execute(
                """
                INSERT INTO player (fivb_player_no, name)
                VALUES (%s, %s)
                ON CONFLICT (fivb_player_no) DO UPDATE
                  SET name = COALESCE(EXCLUDED.name, player.name)
                RETURNING player_id;
                """,
                (fivb_no, name),
            )
            return cur.fetchone()["player_id"]

        # fallback bez čísla – hledáme podle name (může být NULL → vložíme „bezejmenného“)
        if name:
            cur.execute("SELECT player_id FROM player WHERE name = %s LIMIT 1;", (name,))
            row = cur.fetchone()
            if row:
                return row["player_id"]

        cur.execute("INSERT INTO player (name) VALUES (%s) RETURNING player_id;", (name,))
        return cur.fetchone()["player_id"]

    def _ensure_distinct_players(self, cur, p1_id: int, p2_id: int,
                             fallback_name: str | None = None) -> tuple[int, int]:
        if p1_id != p2_id:
            return p1_id, p2_id

        # vytvoř odlišného placeholder hráče
        placeholder_name = fallback_name or "Unknown"
        cur.execute("INSERT INTO player (name) VALUES (%s) RETURNING player_id;", (placeholder_name,))
        new_p2 = cur.fetchone()["player_id"]
        return p1_id, new_p2

    def upsert_team(self, cur, p1_id: int, p2_id: int, display_name: Optional[str]) -> int:
        # zajistíme stabilní pořadí (menší id jako player1)
        a, b = sorted((p1_id, p2_id))
        cur.execute(
            """
            INSERT INTO team (player1_id, player2_id, display_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (player1_id, player2_id) DO UPDATE
              SET display_name = COALESCE(EXCLUDED.display_name, team.display_name)
            RETURNING team_id;
            """,
            (a, b, display_name),
        )
        return cur.fetchone()["team_id"]

    def upsert_crawl_run(self, cur, run_date: date) -> int:
        cur.execute(
            """
            INSERT INTO crawl_run (run_date)
            VALUES (%s)
            ON CONFLICT (run_date) DO NOTHING
            RETURNING run_id;
            """,
            (run_date,),
        )
        row = cur.fetchone()
        if row:
            return row["run_id"]
        # pokud existoval, načteme jeho id
        cur.execute("SELECT run_id FROM crawl_run WHERE run_date = %s;", (run_date,))
        return cur.fetchone()["run_id"]

    def upsert_snapshot(self, cur, tournament_id: int, team_id: int, run_id: int,
                        status: str, rank: Optional[int]) -> None:
        cur.execute(
            """
            INSERT INTO tournament_team_snapshot (tournament_id, team_id, run_id, status, rank)
            VALUES (%s, %s, %s, %s::team_status, %s)
            ON CONFLICT (tournament_id, team_id, run_id) DO UPDATE
              SET status = EXCLUDED.status,
                  rank   = EXCLUDED.rank;
            """,
            (tournament_id, team_id, run_id, status, rank),
        )

    # ---- main entrypoint ----
    def persist_snapshots(self, snapshots: Iterable[EventTeamsSnapshot], run_date: date) -> None:
        """
        Uloží celou dávku snapshotů v jedné transakci.
        Idempotentní vůči stejnému `run_date` (ON CONFLICT).
        """
        con = self.cfg.connect()
        try:
            with con:
                with con.cursor() as cur:
                    run_id = self.upsert_crawl_run(cur, run_date)

                    for snap in snapshots:
                        # 1) Event
                        event_id = self.upsert_event(cur, snap.event)

                        # 2) Tournaments
                        tourn_id_map: Dict[int, int] = {}
                        for tref in snap.tournaments:
                            t_id = self.upsert_tournament(cur, event_id, tref)
                            tourn_id_map[tref.tournament_no] = t_id

                        # 3) Teams per tournament
                        for t_no, teams in snap.teams_by_tournament.items():
                            t_id = tourn_id_map.get(t_no)
                            if not t_id:
                                # Může se stát, že turnaj nešel naparsovat – jen přeskoč
                                continue

                            for team in teams:
                                # Players
                                p1_id = self.upsert_player(cur, team.no_player1, None)  # jména hráčů nemusí být v API
                                p2_id = self.upsert_player(cur, team.no_player2, None)

                                # NEW: zajisti, že se neshodují
                                fallback = team.name or "Unknown"
                                p1_id, p2_id = self._ensure_distinct_players(cur, p1_id, p2_id, fallback_name=fallback)
                                
                                # Team
                                team_id = self.upsert_team(cur, p1_id, p2_id, team.name or None)

                                # Snapshot
                                self.upsert_snapshot(cur, t_id, team_id, run_id, team.status, team.rank)
        finally:
            con.close()
