from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import psycopg2, psycopg2.extras
import os

app = FastAPI()
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
                {"request": request, "teams": [], "fivb_no": fivb_no, "gender": None, "tournament_name": None})
        cur.execute("""
        WITH cur AS (
          SELECT DISTINCT ON (tts.team_id)
            tts.team_id, tts.rank, tts.status AS current_status, cr.run_date AS as_of_date
          FROM tournament_team_snapshot tts
          JOIN crawl_run cr ON cr.run_id = tts.run_id
          WHERE tts.tournament_id = %s
          ORDER BY tts.team_id, cr.run_date DESC
        )
        SELECT tm.team_id, p1.name AS player1, p2.name AS player2, tm.display_name,
               cur.rank, cur.current_status,
               w.withdrawn_at
        FROM cur
        JOIN team tm ON tm.team_id = cur.team_id
        JOIN player p1 ON p1.player_id = tm.player1_id
        JOIN player p2 ON p2.player_id = tm.player2_id
        LEFT JOIN v_tournament_team_withdrawal w
          ON w.tournament_id = %s AND w.team_id = cur.team_id
        ORDER BY (cur.current_status='Withdrawn') DESC,
                 w.withdrawn_at NULLS LAST,
                 COALESCE(cur.rank, 999999), tm.team_id
        """, (t["tournament_id"], t["tournament_id"]))
        teams = cur.fetchall()
        return templates.TemplateResponse(
        "tournament.html",
        {
            "request": request,
            "teams": teams,
            "fivb_no": fivb_no,
            "gender": t["gender"],
            "tournament_name": t["event_name"]
        }
    )
