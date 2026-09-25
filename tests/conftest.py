import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as bets_app  # noqa: E402
import polymarket  # noqa: E402


def gamma_market(market_id, *, outcomes=("Yes", "No"), tokens=("t-yes", "t-no"), prices=("0.5", "0.5"),
                 closed=False, uma=None):
    """A market shaped like Gamma's responses: array fields are JSON-encoded strings."""
    market = {"id": str(market_id), "question": "Will it happen?", "closed": closed,
              "outcomes": json.dumps(list(outcomes)), "outcomePrices": json.dumps(list(prices)),
              "clobTokenIds": json.dumps(list(tokens))}
    if uma:
        market["umaResolutionStatus"] = uma
    return market


class FakeApi:
    """Answers the two Polymarket calls a refresh makes, from in-memory fixtures."""

    def __init__(self):
        self.markets = {}
        self.midpoints = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        assert url == polymarket.GAMMA + "/markets" and timeout
        self.calls.append(url)
        ids = [v for k, v in params if k == "id"]
        # Gamma omits closed markets unless closed=true is passed.
        closed_only = ("closed", "true") in params
        return FakeResponse([m for i, m in self.markets.items() if i in ids and m["closed"] == closed_only])

    def post(self, url, json=None, timeout=None):
        assert url == polymarket.CLOB + "/midpoints" and timeout
        self.calls.append(url)
        return FakeResponse({t["token_id"]: self.midpoints[t["token_id"]]
                             for t in json if t["token_id"] in self.midpoints})


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.ok = True

    def json(self):
        return self._payload


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(polymarket.requests, "get", fake.get)
    monkeypatch.setattr(polymarket.requests, "post", fake.post)
    return fake


@pytest.fixture
def data_file(tmp_path):
    return tmp_path / "data" / "bets.json"


@pytest.fixture
def client(data_file, api):
    return bets_app.create_app(data_file).test_client()


def bet_form(**overrides):
    """A two-person binary bet: Alice Yes at 20%, Bob No at 80%, both for 100."""
    form = {"title": "Test bet", "type": "binary", "bet_date": "2026-09-01", "expiry_date": "2099-12-31",
            "pos_id": ["", ""], "pos_name": ["Alice", "Bob"], "pos_outcome": ["yes", "no"],
            "pos_amount": ["100", "100"], "pos_format": ["probability", "probability"], "pos_value": ["20", "80"]}
    form.update(overrides)
    return form


def saved(data_file):
    return json.loads(data_file.read_text())
