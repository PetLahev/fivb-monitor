from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from fastapi import HTTPException
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from api import router as api_router
from datetime import datetime
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
def homepage(request: Request, year: int | None = None):
    with db() as conn, conn.cursor() as cur:
        # 1) get available years
        cur.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM start_date)::int AS year
            FROM event
            ORDER BY year DESC;
        """)
        year_rows = cur.fetchall()
        years = [r["year"] for r in year_rows]

        if not years:
            # no data – just empty page
            return templates.TemplateResponse(
                "index.html",
                {
                    "request": request,
                    "events": [],
                    "years": [],
                    "current_year": None,
                },
            )
        
        # 2) get current_year
        if year is None:            
            current_year = years[0]
        else:
            current_year = year if year in years else years[0]

        # 3) events for current_year
        cur.execute("""
            SELECT e.event_id, e.fivb_event_no, e.code, e.name, e.start_date, e.end_date,
                   MAX(t.fivb_tournament_no) FILTER (WHERE t.gender='M') AS men_no,
                   MAX(t.fivb_tournament_no) FILTER (WHERE t.gender='W') AS women_no
            FROM event e
            LEFT JOIN tournament t ON t.event_id = e.event_id
            WHERE EXTRACT(YEAR FROM e.start_date)::int = %s
            GROUP BY e.event_id
            ORDER BY e.start_date DESC, e.event_id DESC;
        """, (current_year,))
        rows = cur.fetchall()
    
        return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": rows,
            "years": years,
            "current_year": current_year,
        },
    )

@app.get("/tournament/{fivb_no}", response_class=HTMLResponse)
def tournament_detail(request: Request, fivb_no: int):
    with db() as conn, conn.cursor() as cur:
        # find tournament based on FIVB id
        cur.execute("""
            SELECT t.tournament_id,
                t.gender,
                t.event_id,
                e.name AS event_name
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
                    "other_fivb_no": None,
                    "other_gender": None,
                },
            )

        # try to find the tournament of opposite gender (M ↔ W) inside the same event
        other_fivb_no = None
        other_gender = None
        if t["gender"] in ("M", "W"):
            cur.execute("""
                SELECT fivb_tournament_no, gender
                FROM tournament
                WHERE event_id = %s
                AND gender IS NOT NULL
                AND gender <> %s
                LIMIT 1;
            """, (t["event_id"], t["gender"]))
            other = cur.fetchone()
            if other:
                other_fivb_no = other["fivb_tournament_no"]
                other_gender = other["gender"]

        # the teams in the tournament for current run
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
          w.withdrawn_at,
          MAX(cur.as_of_date) OVER () AS last_checked
        FROM cur
        JOIN team tm ON tm.team_id = cur.team_id
        JOIN player p1 ON p1.player_id = tm.player1_id
        JOIN player p2 ON p2.player_id = tm.player2_id
        LEFT JOIN v_tournament_team_withdrawal w
          ON w.tournament_id = %s AND w.team_id = cur.team_id
        ORDER BY
          (cur.current_status IN ('Withdrawn','WithdrawnWithMedicalCert','Deleted')) DESC,
          w.withdrawn_at NULLS LAST,
          tm.team_id;
        """, (t["tournament_id"], t["tournament_id"]))
        teams = cur.fetchall()

        last_checked = teams[0]["last_checked"] if teams else None

        return templates.TemplateResponse(
            "tournament.html",
            {
                "request": request,
                "teams": teams,
                "fivb_no": fivb_no,
                "gender": t["gender"],
                "tournament_name": t["event_name"],
                "other_fivb_no": other_fivb_no,
                "other_gender": other_gender,
                "last_checked": last_checked,
            },
        )

def flag_url(nf: Optional[str]) -> Optional[str]:
    """
    nf = 3-letter country/federation code (foe example: CZE, BRA, USA, ENG, WAL)
    Returns local url path to the flag stored inside /static/flags.
    """
    if not nf:
        return None
    nf = nf.upper()
    return f"/static/flags/{nf}.svg"


templates.env.globals["flag_url"] = flag_url

def format_date(value):
    """
    Format date or ISO string to 'DD MMM YYYY'
    Example: 2025-11-22 -> '22 Nov 2025'
    """
    if not value:
        return ""
    
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value).date()
        except ValueError:
            return value  # fallback

    # '22 Nov 2025'
    return value.strftime("%d %b %Y")

# make filter available in templates
templates.env.filters["format_date"] = format_date