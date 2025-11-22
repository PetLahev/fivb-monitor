from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from fastapi import HTTPException
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from api import router as api_router
import psycopg2, psycopg2.extras
import os

class WithdrawalTeam(BaseModel):
    team_id: int
    display_name: Optional[str]

    player1_name: Optional[str]
    player2_name: Optional[str]
    fivb_player1_no: Optional[int]
    fivb_player2_no: Optional[int]

    country_code: Optional[str]
    withdrawn_at: Optional[str]

    tournament_id: int
    tournament_fivb_no: int
    event_id: int
    event_fivb_no: int

app = FastAPI()
app.include_router(api_router, prefix="/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def db():
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fivb_monitor")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)

@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    sql = """
    SELECT e.event_id, e.fivb_event_no, e.code, e.name, e.start_date, e.end_date,
           MAX(t.fivb_tournament_no) FILTER (WHERE t.gender='M') AS men_no,
           MAX(t.fivb_tournament_no) FILTER (WHERE t.gender='W') AS women_no
    FROM event e
    LEFT JOIN tournament t ON t.event_id = e.event_id
    GROUP BY e.event_id
    ORDER BY e.start_date DESC, e.event_id DESC
    """
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return templates.TemplateResponse("index.html", {"request": request, "events": rows})

@app.get("/tournament/{fivb_no}", response_class=HTMLResponse)
def tournament_detail(request: Request, fivb_no: int):
    with db() as conn, conn.cursor() as cur:
        # najdeme turnaj podle FIVB čísla
        cur.execute("""
            SELECT t.tournament_id, t.gender, e.name AS event_name
            FROM tournament t
            JOIN event e ON e.event_id = t.event_id
            WHERE t.fivb_tournament_no = %s
        """, (fivb_no,))
        t = cur.fetchone()

        if not t:
            return templates.TemplateResponse(
                "tournament.html",
                {
                    "request": request,
                    "teams": [],
                    "fivb_no": fivb_no,
                    "gender": None,
                    "tournament_name": None,
                },
            )

        # aktuální stav týmů pro daný turnaj
        cur.execute("""
        WITH cur AS (
          SELECT DISTINCT ON (tts.team_id)
            tts.team_id,
            tts.status AS current_status,
            cr.run_date AS as_of_date
          FROM tournament_team_snapshot tts
          JOIN crawl_run cr ON cr.run_id = tts.run_id
          WHERE tts.tournament_id = %s
          ORDER BY tts.team_id, cr.run_date DESC
        )
        SELECT
          tm.team_id,
          p1.name AS player1,
          p2.name AS player2,
          tm.display_name,
          tm.country_code,
          cur.current_status,
          w.withdrawn_at
        FROM cur
        JOIN team tm ON tm.team_id = cur.team_id
        JOIN player p1 ON p1.player_id = tm.player1_id
        JOIN player p2 ON p2.player_id = tm.player2_id
        LEFT JOIN v_tournament_team_withdrawal w
          ON w.tournament_id = %s AND w.team_id = cur.team_id
        ORDER BY
          (cur.current_status IN ('Withdrawn','WithdrawnWithMedicalCert')) DESC,
          w.withdrawn_at NULLS LAST,
          tm.team_id;
        """, (t["tournament_id"], t["tournament_id"]))
        teams = cur.fetchall()

        return templates.TemplateResponse(
            "tournament.html",
            {
                "request": request,
                "teams": teams,
                "fivb_no": fivb_no,
                "gender": t["gender"],
                "tournament_name": t["event_name"],
            },
        )

def flag_url(nf: Optional[str]) -> Optional[str]:
    """
    nf = 3písmenný country/federation kód (např. CZE, BRA, USA, ENG, WAL)
    Vrací URL na lokální malou vlajku v /static/flags.
    """
    if not nf:
        return None
    nf = nf.upper()
    return f"/static/flags/{nf}.svg"


templates.env.globals["flag_url"] = flag_url

