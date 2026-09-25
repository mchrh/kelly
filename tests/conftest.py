import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as bets_app  # noqa: E402
import polymarket  # noqa: E402

YES_TOKEN = "84025047502004199729959028241670080426984962626909855338967850748255609972678"
NO_TOKEN = "91442805272044418504961279769419002065834657032036004243246130752097063369765"


def gamma_market(market_id, slug, question, *, outcomes=("Yes", "No"), tokens=None, prices=("0.145", "0.855"),
                 closed=False, uma=None, end="2026-09-25T18:45:00Z"):
    """A market object shaped like Gamma's responses (array fields are JSON-encoded strings)."""
    tokens = tokens or (f"{market_id}1", f"{market_id}2")
    market = {
        "id": str(market_id),
        "question": question,
        "conditionId": f"0xcond{market_id}",
        "slug": slug,
        "endDate": end,
        "endDateIso": end[:10],
        "outcomes": json.dumps(list(outcomes)),
        "outcomePrices": json.dumps(list(prices)),
        "clobTokenIds": json.dumps(list(tokens)),
        "active": True,
        "closed": closed,
        "enableOrderBook": True,
    }
    if uma is not None:
        market["umaResolutionStatus"] = uma
    return market


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return self._payload


class FakeApi:
    """Routes requests aimed at the Polymarket hosts to in-memory fixtures."""

    def __init__(self):
        self.events = {}
        self.markets = {}
        self.midpoints = {}
        self.fail_markets = False
        self.fail_midpoints = False
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        assert timeout, "requests must use a timeout"
        if url.startswith(polymarket.GAMMA + "/events/slug/"):
            slug = url.rsplit("/", 1)[1]
            if slug not in self.events:
                return FakeResponse({"type": "not found"}, 404)
            return FakeResponse(self.events[slug])
        if url == polymarket.GAMMA + "/markets":
            if self.fail_markets:
                return FakeResponse({"error": "down"}, 503)
            ids = [v for k, v in params if k == "id"]
            closed_only = ("closed", "true") in params
            # Gamma omits closed markets unless closed=true, and open ones when it is set.
            found = [m for i, m in self.markets.items() if i in ids and m["closed"] == closed_only]
            return FakeResponse(found)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        assert timeout, "requests must use a timeout"
        assert url == polymarket.CLOB + "/midpoints"
        if self.fail_midpoints:
            return FakeResponse({"error": "down"}, 500)
        return FakeResponse({t["token_id"]: self.midpoints[t["token_id"]]
                             for t in json if t["token_id"] in self.midpoints})


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
def app(data_file, api):
    return bets_app.create_app(data_file)


@pytest.fixture
def client(app):
    return app.test_client()


def bet_form(**overrides):
    form = {
        "title": "Test bet",
        "type": "binary",
        "bet_date": "2026-09-01",
        "expiry_date": "2099-12-31",
        "notes": "",
        "pos_id": ["", ""],
        "pos_name": ["Alice", "Bob"],
        "pos_outcome": ["yes", "no"],
        "pos_amount": ["100", "80"],
        "pos_format": ["decimal_odds", "decimal_odds"],
        "pos_value": ["2.50", "2.00"],
    }
    form.update(overrides)
    return form


def saved(data_file):
    return json.loads(data_file.read_text())


@pytest.fixture
def linked_bet(client, api, data_file):
    """Create a bet linked to a Yes/No market whose outcome order is Yes, No."""
    api.markets["500"] = gamma_market(500, "will-it-happen", "Will it happen?", tokens=(YES_TOKEN, NO_TOKEN))
    api.midpoints = {YES_TOKEN: "0.6", NO_TOKEN: "0.4"}
    link = {"url": "https://polymarket.com/event/will-it-happen", "market_id": "500",
            "condition_id": "0xcond500", "question": "Will it happen?",
            "tokens": {"yes": YES_TOKEN, "no": NO_TOKEN}}
    response = client.post("/bets", data=bet_form(polymarket_json=json.dumps(link)))
    assert response.status_code == 200
    return saved(data_file)["bets"][0]
