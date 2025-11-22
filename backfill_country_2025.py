from __future__ import annotations
from datetime import date

import psycopg2.extras

from fivb_scraper import HttpClient, FIVBService
from db_store import DBConfig


def main():
    cfg = DBConfig()
    con = cfg.connect()
    client = HttpClient()
    # backfill může mít vyšší limit requestů, ale pořád rozumný
    svc = FIVBService(client, max_requests_per_run=500)

    try:
        with con:
            with con.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                # 1) Najdeme turnaje v roce 2025, kde mají týmy country_code = NULL
                cur.execute(
                    """
                    SELECT DISTINCT ON (t.tournament_id)
                    t.tournament_id,
                    t.fivb_tournament_no
                    FROM tournament t
                    JOIN event e ON e.event_id = t.event_id
                    JOIN tournament_team_snapshot tts ON tts.tournament_id = t.tournament_id
                    JOIN team tm ON tm.team_id = tts.team_id
                    WHERE tm.country_code IS NULL
                    AND t.fivb_tournament_no IS NOT NULL
                    AND e.start_date >= DATE '2025-01-01'
                    AND e.start_date <  DATE '2026-01-01'
                    ORDER BY t.tournament_id, e.start_date, t.fivb_tournament_no;
                    """
                )
                tournaments = cur.fetchall()

                print(f"Found {len(tournaments)} tournaments in 2025 with teams missing country_code.")

                for row in tournaments:
                    tid = row["tournament_id"]
                    fivb_no = row["fivb_tournament_no"]
                    print(f"\n=== Tournament {fivb_no} (local id {tid}) ===")

                    # 2) Stáhneme aktuální seznam týmů z FIVB pro tento turnaj
                    try:
                        teams = svc.fetch_teams_for_tournament(fivb_no)
                    except Exception as exc:
                        print(f"  !! Error fetching teams for {fivb_no}: {exc}")
                        continue

                    # Mapa (NoPlayer1, NoPlayer2) -> country_code z VIS
                    cc_map = {}
                    for bt in teams:
                        if (
                            bt.no_player1
                            and bt.no_player2
                            and bt.country_code
                        ):
                            key = (bt.no_player1, bt.no_player2)
                            cc_map[key] = bt.country_code

                    if not cc_map:
                        print("  No country codes returned from VIS for this tournament.")
                        continue

                    # 3) Z DB zjistíme týmy pro tento turnaj, které ještě nemají country_code
                    cur.execute(
                        """
                        SELECT DISTINCT
                          tm.team_id,
                          p1.fivb_player_no AS no1,
                          p2.fivb_player_no AS no2,
                          tm.country_code
                        FROM tournament_team_snapshot tts
                        JOIN team tm
                          ON tm.team_id = tts.team_id
                        JOIN player p1
                          ON p1.player_id = tm.player1_id
                        JOIN player p2
                          ON p2.player_id = tm.player2_id
                        WHERE tts.tournament_id = %s
                          AND tm.country_code IS NULL;
                        """,
                        (tid,),
                    )
                    db_teams = cur.fetchall()

                    updated_count = 0
                    for dt in db_teams:
                        no1 = dt["no1"]
                        no2 = dt["no2"]
                        if not no1 or not no2:
                            continue

                        key = (no1, no2)
                        cc = cc_map.get(key)
                        if cc:
                            cur.execute(
                                """
                                UPDATE team
                                   SET country_code = %s
                                 WHERE team_id = %s
                                   AND country_code IS NULL;
                                """,
                                (cc, dt["team_id"]),
                            )
                            updated_count += 1

                    print(f"  Updated {updated_count} teams with country_code.")

    finally:
        con.close()


if __name__ == "__main__":
    main()
