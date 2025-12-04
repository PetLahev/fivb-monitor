# test_api_tcode.py
import pytest

from fastapi.testclient import TestClient

import api
from main import app  # FastAPI application

def test_decode_tcode_valid_mita2025():
    gender, event_code, year, db_code = api._decode_tcode("MITA2025")
    assert gender == "M"
    assert event_code == "ITA"
    assert year == "2025"
    assert db_code == "BVB-ITA2025"

    gender, event_code, year, db_code = api._decode_tcode("wita2025")
    assert gender == "W"
    assert event_code == "ITA"
    assert year == "2025"
    assert db_code == "BVB-ITA2025"


def test_decode_tcode_invalid_prefix():
    with pytest.raises(ValueError):
        api._decode_tcode("XITA2025")


def test_api_tcode_invalid_returns_400():
    client = TestClient(app)
    resp = client.get("/api/tcode/ABC/withdrawals")
    assert resp.status_code == 400
    body = resp.json()
    assert "Invalid" in body["detail"]
