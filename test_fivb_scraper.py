import builtins
import requests
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import xml.etree.ElementTree as ET

import fivb_scraper as fs

def _xml(s: str) -> ET.Element:
    return ET.fromstring(s)

def test_parse_event_list_and_window(monkeypatch):
    # Připravíme XML s 3 eventy: včerejší, za 10 dní, za 40 dní
    today = fs._today()
    xml = f"""
    <Response>
      <Event No="100" Code="EV1" Name="Past" StartDate="{(today - timedelta(days=1)).isoformat()}" EndDate="{(today + timedelta(days=1)).isoformat()}"/>
      <Event No="101" Code="EV2" Name="Soon" StartDate="{(today + timedelta(days=10)).isoformat()}" EndDate="{(today + timedelta(days=12)).isoformat()}"/>
      <Event No="102" Code="EV3" Name="Far" StartDate="{(today + timedelta(days=40)).isoformat()}" EndDate="{(today + timedelta(days=41)).isoformat()}"/>
    </Response>
    """
    hc = fs.HttpClient()
    svc = fs.FIVBService(hc, max_requests_per_run=20)

    with patch.object(hc, "get_xml", return_value=_xml(xml)) as m:
        events = svc.fetch_upcoming_events(year=today.year, window_days=28)
        # Jen "Soon" zůstane (je v budoucnu <= 28 dnů)
        assert len(events) == 1
        assert events[0].no == 101

def test_event_tournaments_and_teams_flow(monkeypatch):
    # Arrange: 1 event (No=555), 2 turnaje (M, W), a pár týmů
    event_list_xml = """
    <Response>
      <Event No="555" Code="X" Name="Test" StartDate="2099-01-10" EndDate="2099-01-12"/>
    </Response>
    """
    event_detail_xml = """
    <Response>
      <Event No="555">
        <BeachTournament No="8136" Gender="M"/>
        <BeachTournament No="8137" Gender="W"/>
      </Event>
    </Response>
    """
    team_registered_xml = """
    <Response>
      <BeachTeam Name="Player A / Player B" Rank="12" NoPlayer1="111" NoPlayer2="222"/>
    </Response>
    """
    team_withdrawn_xml = """
    <Response>
      <BeachTeam Name="Team X" Rank="34" NoPlayer1="333" NoPlayer2="444"/>
    </Response>
    """

    hc = fs.HttpClient()
    svc = fs.FIVBService(hc, max_requests_per_run=20)

    # Mockování pořadí volání:
    # 1) GetEventList
    # 2) GetEvent(555)
    # 3) Registered(8136), Withdrawn(8136)
    # 4) Registered(8137), Withdrawn(8137)
    sequence = [
      _xml(event_list_xml),      # GetEventList
      _xml(event_detail_xml),    # GetEvent(555)

      # 8136: Registered, Withdrawn, WithdrawnWithMedicalCert
      _xml(team_registered_xml),
      _xml(team_withdrawn_xml),
      _xml(team_withdrawn_xml),  # můžeme znovu použít withdrawn

      # 8137: Registered, Withdrawn, WithdrawnWithMedicalCert
      _xml(team_registered_xml),
      _xml(team_withdrawn_xml),
      _xml(team_withdrawn_xml),  # znovu Withdrawn (nebo klidně Registered, je to jedno)
    ]

    def side_effect(_):
        return sequence.pop(0)

    with patch.object(hc, "get_xml", side_effect=side_effect):
        # Aby event byl v okně 28 dnů, přenastavíme helper fetch_upcoming_events tak,
        # že vrátí jeden předpřipravený event (vyhneme se závislosti na dnech)
        with patch.object(fs, "_today", return_value=date(2099, 1, 1)):
            snapshots = svc.run(2099)

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.event.no == 555
    assert len(snap.tournaments) == 2
    assert 8136 in snap.teams_by_tournament
    assert 8137 in snap.teams_by_tournament
    assert len(snap.teams_by_tournament[8136]) == 2  # Registered + Withdrawn
    assert len(snap.teams_by_tournament[8137]) == 2

def test_retry_logic_waits_and_limits(monkeypatch):
    hc = fs.HttpClient(retry_wait_seconds=1, max_attempts=3)  # zkráceno pro test
    svc = fs.FIVBService(hc, max_requests_per_run=2)

    # fake response objekt
    class FakeResp:
        def __init__(self, status_code=200, text="<Response></Response>"):
            self.status_code = status_code
            self.text = text

    calls = {"i": 0}
    def flaky_session_get(url, headers=None, timeout=None):
        calls["i"] += 1
        if calls["i"] < 3:
            # simuluj dočasný výpadek (non-200 nebo exception, obojí by fungovalo)
            raise requests.RequestException("temporary failure")
        return FakeResp(200, "<Response></Response>")

    # mockni nízkou vrstvu => retry uvnitř HttpClient.get_xml zůstane aktivní
    monkeypatch.setattr(hc.session, "get", flaky_session_get)

    url = fs.q_get_event_list(date(2099,1,1), date(2099,12,31))
    svc._track()  # simulace 1. requestu kvůli limitu

    # zrychlíme čekání mezi retry
    with patch("time.sleep") as sleep_mock:
        root = hc.get_xml(url)
        assert isinstance(root, ET.Element)
        # 2 selhání => 2 pauzy
        assert sleep_mock.call_count == 2
        assert calls["i"] == 3

def test_max_request_cap(monkeypatch):
    hc = fs.HttpClient()
    svc = fs.FIVBService(hc, max_requests_per_run=1)
    with patch.object(hc, "get_xml", return_value=_xml("<Response/>")):
        # první průchod fetch_upcoming_events volá _track() -> 1
        # Druhé _track() už přeteče
        svc.fetch_upcoming_events(year=2099)
        try:
            svc.fetch_event_tournaments(123)
            assert False, "mělo vyhodit RuntimeError pro překročení limitu"
        except RuntimeError:
            pass

def test_parse_event_tournaments_from_content():
    # Content jako element s raw XML
    xml = """
    <Response>
      <Event No="555"/>
      <Content>
        <![CDATA[
          <Event>
            <BeachTournament No="8137" Gender="0" />
            <BeachTournament No="8136" Gender="1" />
          </Event>
        ]]>
      </Content>
    </Response>
    """
    root = ET.fromstring(xml)
    refs = fs.parse_event_tournaments(root)
    nos = sorted([r.tournament_no for r in refs])
    assert nos == [8136, 8137]
    genders = {r.tournament_no: r.gender for r in refs}
    # podle naší mapy {"1":"M","0":"W"}:
    assert genders[8136] == "M"
    assert genders[8137] == "W"

def test_tournament_without_teams_does_not_break(monkeypatch):
    hc = fs.HttpClient()
    svc = fs.FIVBService(hc, max_requests_per_run=10)

    # 1 event v okně, 1 turnaj, oba listy vrátí prázdné XML
    event_list_xml = """
    <Response>
      <Event No="555" Code="X" Name="Test" StartDate="2099-01-10" EndDate="2099-01-12"/>
    </Response>
    """
    event_detail_xml = """
    <Response>
      <Event No="555">
        <BeachTournament No="9001" Gender="M"/>
      </Event>
    </Response>
    """
    empty_teams_xml = "<Response></Response>"

    seq = [
      ET.fromstring(event_list_xml),
      ET.fromstring(event_detail_xml),
      ET.fromstring(empty_teams_xml),  # Registered
      ET.fromstring(empty_teams_xml),  # Withdrawn
      ET.fromstring(empty_teams_xml),  # WithdrawnWithMedicalCert
    ]

    def side(_):
        return seq.pop(0)

    with patch.object(hc, "get_xml", side_effect=side):
        with patch.object(fs, "_today", return_value=date(2099, 1, 1)):
            snaps = svc.run(2099)

    assert len(snaps) == 1
    snap = snaps[0]
    assert 9001 in snap.teams_by_tournament
    assert snap.teams_by_tournament[9001] == []  # prázdný seznam, ale žádný crash

