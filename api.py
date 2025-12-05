# api.py
from typing import List, Optional, Tuple
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException
import psycopg2, psycopg2.extras
import os

router = APIRouter()


def db():
    """
    Simple helper to connect to database.
    Same logic like in the main.py (DATABASE_URL or local default).
    """
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fivb_monitor")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)


class WithdrawalTeam(BaseModel):
    # our internal ID of a teams
    team_id: int

    # names a FIVB ID players
    display_name: Optional[str]
    player1_name: Optional[str]
    player2_name: Optional[str]
    fivb_player1_no: Optional[int]
    fivb_player2_no: Optional[int]

    # country and date of withdrawal
    country_code: Optional[str]
    withdrawn_at: Optional[str]  # ISO string "YYYY-MM-DD" nebo None
    withdrawn_status: Optional[str]  # 'Withdrawn'/'WithdrawnWithMedicalCert'/'Deleted'

    # Tournaments/events identification for pairing
    tournament_id: int
    tournament_fivb_no: int
    event_id: int
    event_fivb_no: int

    # last check by scraper (date)
    last_checked: Optional[str]


# ----------------- helper pro TCODE -----------------


def _decode_tcode(tcode: str) -> Tuple[str, str, str, str]:
    """
    Decodes the 'TCODE' type like MITA2025 / WITA2025.

    Returns (gender, event_code_part, year_part, db_event_code),
    where db_event_code equals our event.code, for example "BVB-ITA2025".
    """
    tcode = tcode.upper().strip()
    if len(tcode) < 6:
        raise ValueError("TCODE too short")

    gender_char = tcode[0]
    if gender_char not in ("M", "W"):
        raise ValueError("Invalid gender prefix in TCODE")

    gender = "M" if gender_char == "M" else "W"
    event_code_part = tcode[1:4]   # e.g. ITA
    year_part = tcode[4:]          # e.g. 2025

    db_event_code = f"BVB-{event_code_part}{year_part}"
    return gender, event_code_part, year_part, db_event_code


# ----------------- API FIVB tournament_no -----------------


@router.get("/tournament/{fivb_no}/withdrawals", response_model=List[WithdrawalTeam])
def api_tournament_withdrawals(fivb_no: int):
    """
    JSON API for the withdrawn teams from given tournament (FIVB tournament No).
    Returns both players with FIVB id, our internal ID, FIVB event/tournament, country and withdrawal date.
    """
    with db() as conn, conn.cursor() as cur:
        # 1) Find the tournament and its event id
        cur.execute(
            """
            SELECT 
                t.tournament_id,
                t.fivb_tournament_no,
                e.event_id,
                e.fivb_event_no
            FROM tournament t
            JOIN event e ON e.event_id = t.event_id
            WHERE t.fivb_tournament_no = %s
            """,
            (fivb_no,),
        )
        t = cur.fetchone()

        if not t:
            raise HTTPException(status_code=404, detail="Tournament not found")

        tournament_id = t["tournament_id"]
        tournament_fivb_no = t["fivb_tournament_no"]
        event_id = t["event_id"]
        event_fivb_no = t["fivb_event_no"]

        # last check for the tournament
        cur.execute(
            """
            SELECT MAX(cr.run_date) AS last_checked
            FROM tournament_team_snapshot tts
            JOIN crawl_run cr ON cr.run_id = tts.run_id
            WHERE tts.tournament_id = %s
            """,
            (tournament_id,),
        )
        row = cur.fetchone()
        last_checked = row["last_checked"] if row else None

        # 2) Gets all withdrawn teams from th view
        cur.execute(
            """
            SELECT
              tm.team_id,
              tm.display_name,
              tm.country_code,

              p1.name AS player1_name,
              p2.name AS player2_name,
              p1.fivb_player_no AS fivb_player1_no,
              p2.fivb_player_no AS fivb_player2_no,

              w.withdrawn_at,
              w.withdrawn_status
            FROM v_tournament_team_withdrawal w
            JOIN team   tm ON tm.team_id   = w.team_id
            JOIN player p1 ON p1.player_id = tm.player1_id
            JOIN player p2 ON p2.player_id = tm.player2_id
            WHERE w.tournament_id = %s
            ORDER BY w.withdrawn_at, tm.team_id;
            """,
            (tournament_id,),
        )

        rows = cur.fetchall()

        return [
            WithdrawalTeam(
                team_id=r["team_id"],
                display_name=r["display_name"],
                player1_name=r["player1_name"],
                player2_name=r["player2_name"],
                fivb_player1_no=r["fivb_player1_no"],
                fivb_player2_no=r["fivb_player2_no"],
                country_code=r["country_code"],
                withdrawn_at=r["withdrawn_at"].isoformat() if r["withdrawn_at"] else None,
                withdrawn_status=r["withdrawn_status"],
                tournament_id=tournament_id,
                tournament_fivb_no=tournament_fivb_no,
                event_id=event_id,
                event_fivb_no=event_fivb_no,
                last_checked=last_checked.isoformat() if last_checked else None,
            )
            for r in rows
        ]


# ----------------- API TCODE (MITA2025 / WITA2025) -----------------


@router.get("/tcode/{tcode}/withdrawals", response_model=List[WithdrawalTeam])
def api_tcode_withdrawals(tcode: str):
    """
    Compatible API with the 'TCODE' format used in fivb.12ndr.at
    (e.g. MITA2025 for men Itapema 2025, WITA2025 for women).

    1. Parses the 'TCODE' → gender + event code + year
    2. Finds event inside the database based on event.code (BVB-ITA2025)
    3. Finds tournament inside the event based on given gender
    4. Returns JSON like in /api/tournament/{fivb_no}/withdrawals
    """
    try:
        gender, event_code_part, year_part, db_event_code = _decode_tcode(tcode)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))

    with db() as conn, conn.cursor() as cur:
        # 1) Find the event based on given event code
        cur.execute(
            """
            SELECT event_id, fivb_event_no
            FROM event
            WHERE code = %s
            """,
            (db_event_code,),
        )
        ev = cur.fetchone()

        if not ev:
            raise HTTPException(status_code=404, detail="Event not found for this tcode")

        # 2) Find the tournament inside an event according to the gender
        cur.execute(
            """
            SELECT tournament_id, fivb_tournament_no
            FROM tournament
            WHERE event_id = %s AND gender = %s
            """,
            (ev["event_id"], gender),
        )
        t = cur.fetchone()

        if not t:
            raise HTTPException(status_code=404, detail="Tournament for this gender not found")

        tournament_id = t["tournament_id"]
        tournament_fivb_no = t["fivb_tournament_no"]
        event_id = ev["event_id"]
        event_fivb_no = ev["fivb_event_no"]

        # 3) Withdrawal rows
        cur.execute(
            """
            SELECT
              tm.team_id,
              tm.display_name,
              tm.country_code,

              p1.name AS player1_name,
              p2.name AS player2_name,
              p1.fivb_player_no AS fivb_player1_no,
              p2.fivb_player_no AS fivb_player2_no,

              w.withdrawn_at,
              w.withdrawn_status
            FROM v_tournament_team_withdrawal w
            JOIN team   tm ON tm.team_id   = w.team_id
            JOIN player p1 ON p1.player_id = tm.player1_id
            JOIN player p2 ON p2.player_id = tm.player2_id
            WHERE w.tournament_id = %s
            ORDER BY w.withdrawn_at, tm.team_id;
            """,
            (tournament_id,),
        )

        rows = cur.fetchall()

        # 4) last_checked for that tournament
        cur.execute(
            """
            SELECT MAX(cr.run_date) AS last_checked
            FROM tournament_team_snapshot tts
            JOIN crawl_run cr ON cr.run_id = tts.run_id
            WHERE tts.tournament_id = %s
            """,
            (tournament_id,),
        )
        row = cur.fetchone()
        last_checked = row["last_checked"] if row else None

        return [
            WithdrawalTeam(
                team_id=r["team_id"],
                display_name=r["display_name"],
                player1_name=r["player1_name"],
                player2_name=r["player2_name"],
                fivb_player1_no=r["fivb_player1_no"],
                fivb_player2_no=r["fivb_player2_no"],
                country_code=r["country_code"],
                withdrawn_at=r["withdrawn_at"].isoformat() if r["withdrawn_at"] else None,
                withdrawn_status=r["withdrawn_status"],
                tournament_id=tournament_id,
                tournament_fivb_no=tournament_fivb_no,
                event_id=event_id,
                event_fivb_no=event_fivb_no,
                last_checked=last_checked.isoformat() if last_checked else None,
            )
            for r in rows
        ]
