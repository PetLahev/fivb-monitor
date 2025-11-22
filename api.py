# api.py
from typing import List, Optional
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException
import psycopg2, psycopg2.extras
import os

router = APIRouter()


def db():
    """
    Jednoduchý helper na připojení k DB.
    Používá stejnou logiku jako main.py (DATABASE_URL nebo local default).
    """
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fivb_monitor")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)


class WithdrawalTeam(BaseModel):
    # náš interní ID týmu (může se hodit i pro HTML část)
    team_id: int

    # názvy a FIVB ID hráčů
    display_name: Optional[str]
    player1_name: Optional[str]
    player2_name: Optional[str]
    fivb_player1_no: Optional[int]
    fivb_player2_no: Optional[int]

    # země a datum odhlášení
    country_code: Optional[str]
    withdrawn_at: Optional[str]  # ISO string "YYYY-MM-DD" nebo None

    # identifikace turnaje/eventu pro párování
    tournament_id: int
    tournament_fivb_no: int
    event_id: int
    event_fivb_no: int


@router.get("/tournament/{fivb_no}/withdrawals", response_model=List[WithdrawalTeam])
def api_tournament_withdrawals(fivb_no: int):
    """
    JSON API pro odhlášené týmy z daného turnaje (podle FIVB tournament No).
    Vrací dvojici FIVB hráčů, naše ID, FIVB event/turnaj, zemi a datum odhlášení.
    """
    with db() as conn, conn.cursor() as cur:
        # 1) Najdeme turnaj + jeho event
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

        # 2) Vytáhneme všechny odhlášené týmy pro tento turnaj z view
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

              w.withdrawn_at
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
                tournament_id=tournament_id,
                tournament_fivb_no=tournament_fivb_no,
                event_id=event_id,
                event_fivb_no=event_fivb_no,
            )
            for r in rows
        ]
