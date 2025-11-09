from __future__ import annotations
import dataclasses
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Tuple
from datetime import date
from db_store import Storage
import time
import logging
import os
import xml.etree.ElementTree as ET
import urllib.parse
import requests
import html

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Fallback, not critical for server envs using 3.11+

FIVB_XML_ENDPOINT = "https://www.fivb.org/vis2009/XmlRequest.asmx"
DEFAULT_TZ = "Europe/Prague"

# ---------- Logging ----------
logger = logging.getLogger("fivb")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------- Data models ----------
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
    gender: Optional[str]  # 'M'/'W' or None if not provided

@dataclass(frozen=True)
class BeachTeam:
    no_player1: Optional[int]
    no_player2: Optional[int]
    name: str
    rank: Optional[int]
    status: str  # 'Registered' / 'Withdrawn'

@dataclass
class EventTeamsSnapshot:
    event: Event
    tournaments: List[BeachTournamentRef]
    teams_by_tournament: Dict[int, List[BeachTeam]]  # tournament_no -> teams


# ---------- Helpers ----------
def _today(tz: str = DEFAULT_TZ) -> date:
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo(tz)).date()
    return datetime.now().date()

def _encode_request(xml_body: str) -> str:
    return urllib.parse.urlencode({"Request": xml_body})

def _build_url(xml_body: str) -> str:
    return f"{FIVB_XML_ENDPOINT}?{_encode_request(xml_body)}"

def _parse_date_yyyy_mm_dd(value: str) -> date:
    # VIS často vrací 'YYYY-MM-DD' (bez času)
    return datetime.strptime(value[:10], "%Y-%m-%d").date()

def _xml_text(node: Optional[ET.Element], attr: str, default: Optional[str] = None) -> Optional[str]:
    if node is None:
        return default
    return node.attrib.get(attr, default)


# ---------- Network with retries (15 minutes between retries) ----------
class HttpClient:
    def __init__(self, session: Optional[requests.Session] = None,
                 application_id: Optional[str] = "FIVB.12ndr.WithdrawnMonitor",
                 per_attempt_timeout: int = 30,
                 retry_wait_seconds: int = 15 * 60,  # 15 minutes
                 max_attempts: int = 3):
        self.session = session or requests.Session()
        self.per_attempt_timeout = per_attempt_timeout
        self.retry_wait_seconds = retry_wait_seconds
        self.max_attempts = max_attempts
        self.headers = {
            "User-Agent": f"FIVB-Fetcher/1.0 (+python; contact=dev)",
        }
        # Některé instalace VIS doporučují "Application" hlavičku
        if application_id:
            self.headers["Application"] = application_id  # ponecháno konfigurovatelně

    def get_xml(self, url: str) -> ET.Element:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.session.get(url, headers=self.headers, timeout=self.per_attempt_timeout)
                if resp.status_code != 200 or not resp.text.strip():
                    raise requests.RequestException(f"Bad status {resp.status_code} or empty body")
                try:
                    root = ET.fromstring(resp.text)
                except ET.ParseError as pe:
                    raise requests.RequestException(f"XML parse error: {pe}") from pe

                return root
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Attempt {attempt}/{self.max_attempts} failed for {url}: {exc}")
                if attempt < self.max_attempts:
                    logger.info(f"Waiting {self.retry_wait_seconds} seconds before retry...")
                    time.sleep(self.retry_wait_seconds)
        assert last_exc is not None
        raise last_exc


# ---------- FIVB queries ----------
def q_get_event_list(start: date, end: date) -> str:
    xml = (
        "<Requests>"
        "<Request Type='GetEventList' Fields='Code Name StartDate EndDate'>"
        f"<Filter IsVisManaged='True' NoParentEvent='0' HasBeachTournament='True' "
        f"StartDate='{start.isoformat()}' EndDate='{end.isoformat()}' />"
        "</Request>"
        "</Requests>"
    )
    return _build_url(xml)

def q_get_event(no: int) -> str:
    xml = (
        "<Requests>"
        f"<Request Type='GetEvent' No='{no}'/>"
        "</Requests>"
    )
    return _build_url(xml)

def q_get_beach_team_list(no_tournament: int, status: str) -> str:
    xml = (
        "<Requests>"
        "<Request Type='GetBeachTeamList' Fields='NoPlayer1 NoPlayer2 Name Rank'>"
        f"<Filter NoTournament='{no_tournament}' Status='{status}'/>"
        "</Request>"
        "</Requests>"
    )
    return _build_url(xml)


# ---------- Parsers ----------
def parse_event_list(root: ET.Element) -> List[Event]:
    events: List[Event] = []
    # Struktura bývá <Response> <Event ... />
    for ev in root.findall(".//Event"):
        try:
            no = int(ev.attrib.get("No", "0"))
        except ValueError:
            continue
        code = ev.attrib.get("Code", "")
        name = ev.attrib.get("Name", "")
        s = _xml_text(ev, "StartDate", "")
        e = _xml_text(ev, "EndDate", "")
        if not s or not e:
            continue
        start_date = _parse_date_yyyy_mm_dd(s)
        end_date = _parse_date_yyyy_mm_dd(e)
        events.append(Event(no=no, code=code, name=name, start_date=start_date, end_date=end_date))
    return events

def parse_event_tournaments(root: ET.Element) -> List[BeachTournamentRef]:
    refs: List[BeachTournamentRef] = []

    def _collect_from_event_node(event_node: ET.Element):
        for bt in event_node.findall(".//BeachTournament"):
            # No
            try:
                tno = int(bt.attrib.get("No", "0"))
            except ValueError:
                continue

            # Gender může být 'M'/'W' nebo '0'/'1' => zkusíme převést na M/W, ale necháme fallback
            gender_raw = bt.attrib.get("Gender")
            gender_map = {"0": "M", "1": "W"}  # POZN: pokud by to bylo obráceně, jen to prohoď
            gender = gender_map.get(gender_raw, gender_raw)  # 'M'/'W'/None/nezměněné
            refs.append(BeachTournamentRef(tournament_no=tno, gender=gender))

    content_nodes = root.findall(".//Content")
    for c in content_nodes:
        content_text = (c.text or "").strip()
        if not content_text:
            continue
        # pokud je escapované (&lt; &gt;), odescapujeme
        content_text = html.unescape(content_text)
        # bezpečný strip BOM apod.
        content_text = content_text.lstrip("\ufeff").strip()
        if not content_text.startswith("<"):
            # může to být zabalené ještě v nějakém wrapperu – tady případně můžeš doplnit další heuristiku
            pass
        try:
            inner_root = ET.fromstring(content_text)
            _collect_from_event_node(inner_root)
        except ET.ParseError:
            # pokud se nepodaří, pokračuj dalším Contentem
            continue
    if refs:
        return refs
    
    for node in root.iter():
        content_attr = node.attrib.get("Content")
        if not content_attr:
            continue
        content_text = html.unescape(content_attr).lstrip("\ufeff").strip()
        try:
            inner_root = ET.fromstring(content_text)
            _collect_from_event_node(inner_root)
        except ET.ParseError:
            continue

    return refs

def parse_teams(root: ET.Element, status: str) -> List[BeachTeam]:
    teams: List[BeachTeam] = []
    for node in root.findall(".//BeachTeam"):
        name = node.attrib.get("Name", "")
        rank = node.attrib.get("Rank")
        rank_i = int(rank) if rank and rank.isdigit() else None
        p1 = node.attrib.get("NoPlayer1")
        p2 = node.attrib.get("NoPlayer2")
        no1 = int(p1) if p1 and p1.isdigit() else None
        no2 = int(p2) if p2 and p2.isdigit() else None
        teams.append(BeachTeam(no_player1=no1, no_player2=no2, name=name, rank=rank_i, status=status))
    return teams


# ---------- Core orchestration ----------
class FIVBService:
    def __init__(self, client: HttpClient, max_requests_per_run: int = 20):
        self.client = client
        self.max_requests_per_run = max_requests_per_run
        self._request_count = 0

    def _track(self, n: int = 1):
        self._request_count += n
        if self._request_count > self.max_requests_per_run:
            raise RuntimeError(
                f"Překročen limit požadavků ({self._request_count} > {self.max_requests_per_run}). "
                f"Zkrať seznam eventů nebo zvyšte limit s vědomím rizika."
            )

    def fetch_upcoming_events(self, year: int, window_days: int = 28, tz: str = DEFAULT_TZ) -> List[Event]:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        url = q_get_event_list(start, end)
        self._track()
        root = self.client.get_xml(url)
        all_events = parse_event_list(root)

        today = _today(tz)
        future_window_end = today + timedelta(days=window_days)

        # Jen eventy s start_date v budoucnu a do 28 dnů (vč.)
        selected = [
            ev for ev in all_events
            if ev.start_date >= today and ev.start_date <= future_window_end
        ]

        logger.info(f"Nalezeno {len(all_events)} eventů, vybráno {len(selected)} v okně do {window_days} dnů.")
        return selected

    def fetch_event_tournaments(self, event_no: int) -> List[BeachTournamentRef]:
        url = q_get_event(event_no)
        self._track()
        root = self.client.get_xml(url)
        return parse_event_tournaments(root)

    def fetch_teams_for_tournament(self, tournament_no: int) -> List[BeachTeam]:
        teams: List[BeachTeam] = []

        # Registered
        try:
            url_r = q_get_beach_team_list(tournament_no, "Registered")
            self._track()
            root_r = self.client.get_xml(url_r)
            teams.extend(parse_teams(root_r, "Registered"))
        except Exception as e:
            logger.warning(f"[{tournament_no}] Registered fetch failed: {e}")

        # Withdrawn
        try:
            url_w = q_get_beach_team_list(tournament_no, "Withdrawn")
            self._track()
            root_w = self.client.get_xml(url_w)
            teams.extend(parse_teams(root_w, "Withdrawn"))
        except Exception as e:
            logger.warning(f"[{tournament_no}] Withdrawn fetch failed: {e}")

        # Bez týmů = nevadí, vrátíme prázdný seznam
        if not teams:
            logger.info(f"[{tournament_no}] No teams returned (ok, keeping tournament).")
        return teams

    def run(self, year: int) -> List[EventTeamsSnapshot]:
        snapshots: List[EventTeamsSnapshot] = []
        events = self.fetch_upcoming_events(year=year)

        for ev in events:
            try:
                tournaments = self.fetch_event_tournaments(ev.no)
            except Exception as e:
                logger.warning(f"(event {ev.no}) GetEvent failed: {e} — skipping this event")
                continue

            teams_by_tournament: Dict[int, List[BeachTeam]] = {}
            for tref in tournaments:
                teams_by_tournament[tref.tournament_no] = self.fetch_teams_for_tournament(tref.tournament_no)

            snapshots.append(EventTeamsSnapshot(
                event=ev,
                tournaments=tournaments,
                teams_by_tournament=teams_by_tournament
            ))

        return snapshots


# ---------- CLI example ----------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch FIVB beach tournaments upcoming window")
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--window-days", type=int, default=28)
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retry-wait", type=int, default=15*60, help="Seconds between retries (default 900)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--application-id", type=str, default="FIVB.12ndr.WithdrawnMonitor",  help="Identifikátor aplikace (Application header v requestech)")
    parser.add_argument("--tz", type=str, default=DEFAULT_TZ)
    args = parser.parse_args()

    client = HttpClient(
        application_id=args.application_id,
        per_attempt_timeout=args.timeout,
        retry_wait_seconds=args.retry_wait,
        max_attempts=args.max_attempts
    )
    svc = FIVBService(client, max_requests_per_run=args.max_requests)

    snapshots = svc.run(args.year)

        # Výstup do konzole a souboru
    output_lines = []

    for snap in snapshots:
        ev = snap.event
        header = f"\nEvent {ev.no} | {ev.code} | {ev.name} | {ev.start_date} → {ev.end_date}"
        print(header)
        output_lines.append(header)

        for tref in snap.tournaments:
            teams = snap.teams_by_tournament.get(tref.tournament_no, [])
            sub_header = f"  Tournament {tref.tournament_no} ({tref.gender or '?'}) — teams: {len(teams)}"
            print(sub_header)
            output_lines.append(sub_header)

            for t in teams:
                line = f"    [{t.status}] {t.name} (rank={t.rank}, players={t.no_player1}/{t.no_player2})"
                print(line)
                output_lines.append(line)

    # Zapsání do souboru (UTF-8, přepíše při každém běhu)
    #output_path = "teams_output.txt"
    #with open(output_path, "w", encoding="utf-8") as f:
    #    f.write("\n".join(output_lines))

    #print(f"\n✅ Výsledky zapsány do souboru: {output_path}")

    store = Storage()  # vezme DATABASE_URL/PG* proměnné, jinak lokální defaulty
    store.persist_snapshots(snapshots, run_date=date.today())
    print("✅ Uloženo do DB.")

if __name__ == "__main__":
    main()
