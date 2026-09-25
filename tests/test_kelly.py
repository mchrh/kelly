import json
import re
from decimal import Decimal as D
from html import unescape

import app as bets_app
import calc
import polymarket
from conftest import bet_form, gamma_market, saved


def position(outcome, fmt, value, **amount):
    return {"id": "p", "name": "A", "outcome_id": outcome, "entry_format": fmt, "entry_value": value, **amount}


def test_payouts():
    prices = {"yes": D("0.3"), "no": D("0.7")}
    # Binary head-to-head: Yes at 20% on 100 risks 20 to win 80; No at 80% risks 80 to win 20.
    yes = position("yes", "probability", "0.2", payout="100")
    no = position("no", "probability", "0.8", payout="100")
    view = calc.position_view(yes, prices, None)
    assert (view["stake"], view["profit_if_won"], view["value"], view["pl"]) == (D("20.00"), D("80.00"), D("30.00"), D("10.00"))
    view = calc.position_view(no, prices, None)
    assert (view["stake"], view["profit_if_won"], view["value"], view["pl"]) == (D("80.00"), D("20.00"), D("70.00"), D("-10.00"))

    # Multiple choice records a stake: 100 at odds 2.50 or at 40% pays 250.
    for fmt, value in (("decimal_odds", "2.50"), ("probability", "0.4")):
        assert calc.position_view(position("a", fmt, value, stake="100"), None, None)["payout"] == D("250.00")

    # Settled: the winner collects the payout, the loser gets nothing, void refunds the stake.
    won = {"result": "winner", "outcome_id": "yes"}
    assert calc.position_view(yes, None, won)["realized_pl"] == D("80.00")
    assert calc.position_view(no, None, won)["realized_pl"] == D("-80.00")
    assert calc.position_view(no, None, {"result": "void", "outcome_id": None})["final_payout"] == D("80.00")


def test_only_confirmed_polymarket_results_settle():
    tokens = {"yes": "t-yes", "no": "t-no"}
    for unconfirmed in (
        {"prices": ("0.999", "0.001")},                              # near 100% but still trading
        {"closed": True, "prices": ("1", "0")},                       # closed, not resolved
        {"closed": True, "uma": "proposed", "prices": ("1", "0")},    # resolution only proposed
    ):
        assert polymarket.final_result(gamma_market(1, **unconfirmed), tokens) is None

    # The winner is mapped through the saved tokens, whatever order Polymarket lists outcomes in.
    reversed_order = gamma_market(1, outcomes=("No", "Yes"), tokens=("t-no", "t-yes"),
                                  prices=("0", "1"), closed=True, uma="resolved")
    assert polymarket.binary_token_map(reversed_order) == tokens
    assert polymarket.final_result(reversed_order, tokens) == ("winner", "yes")

    # A finalized 50/50 result is flagged for review, never read as a refund.
    fifty = gamma_market(1, prices=("0.5", "0.5"), closed=True, uma="resolved")
    assert polymarket.final_result(fifty, tokens)[0] == "review"


def test_linked_bet_lifecycle(client, api, data_file):
    api.markets["500"] = gamma_market(500)
    api.midpoints = {"t-yes": "0.62", "t-no": "0.41"}
    link = {"url": "https://polymarket.com/event/x", "market_id": "500", "question": "Will it happen?",
            "tokens": {"yes": "t-yes", "no": "t-no"}}
    client.post("/bets", data=bet_form(polymarket_json=json.dumps(link)))
    bet_id = saved(data_file)["bets"][0]["id"]

    # Midpoints are used exactly as returned: No is not derived from Yes, and the pair isn't normalized.
    assert saved(data_file)["bets"][0]["market_quote"]["prices"] == {"yes": "0.62", "no": "0.41"}

    # A partial quote keeps the previous snapshot and shows it as stale.
    api.midpoints = {"t-yes": "0.9"}
    assert "Polymarket · stale" in client.post("/refresh").get_data(as_text=True)
    assert saved(data_file)["bets"][0]["market_quote"]["prices"] == {"yes": "0.62", "no": "0.41"}

    # Editing the bet keeps its link (the hidden form field used to lose it).
    form = client.get(f"/bets/{bet_id}/edit").get_data(as_text=True)
    hidden = unescape(re.search(r'name="polymarket_json" value="([^"]*)"', form).group(1))
    client.post(f"/bets/{bet_id}/edit", data=bet_form(polymarket_json=hidden))
    assert saved(data_file)["bets"][0]["polymarket"]["tokens"] == link["tokens"]

    # A manual price override changes valuation only; the link still settles the bet.
    client.post(f"/bets/{bet_id}/pricing", data={"action": "manual", "prob_yes": "30"})
    api.markets["500"].update(closed=True, umaResolutionStatus="resolved", outcomePrices='["1", "0"]')
    client.post("/refresh")
    bet = saved(data_file)["bets"][0]
    assert bet["pricing_source"] == "manual"
    assert (bet["settlement"]["outcome_id"], bet["settlement"]["source"]) == ("yes", "polymarket")
    results = {r["player"]: (r["result"], r["profit"]) for r in saved(data_file)["results"]}
    assert results == {"Alice": ("won", "80.00"), "Bob": ("lost", "-80.00")}

    # Settled bets are no longer polled, and a manual correction sticks and replaces the log.
    api.calls.clear()
    client.post("/refresh")
    assert api.calls == []
    client.post(f"/bets/{bet_id}/settlement", data={"result": "no"})
    client.post("/refresh")
    assert saved(data_file)["bets"][0]["settlement"]["outcome_id"] == "no"
    assert {r["player"]: r["result"] for r in saved(data_file)["results"]} == {"Alice": "lost", "Bob": "won"}


def test_data_file_safety(data_file, api):
    # A file from before bet amounts, players and results: all are derived on load.
    data_file.parent.mkdir(parents=True)
    old_bet = {
        "id": "old", "title": "Old bet", "type": "binary", "bet_date": "2026-09-01", "expiry_date": "2026-09-02",
        "notes": "", "outcomes": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
        "positions": [
            {"id": "p1", "name": "Alice", "outcome_id": "yes", "stake": "100", "entry_format": "probability", "entry_value": "0.2"},
            {"id": "p2", "name": "Bob", "outcome_id": "no", "stake": "100", "entry_format": "probability", "entry_value": "0.8"},
        ],
        "pricing_source": "manual", "manual_probabilities": None, "manual_updated_at": None, "polymarket": None,
        "market_quote": None, "review": None, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z",
        "settlement": {"result": "winner", "outcome_id": "yes", "source": "manual", "settled_at": "2026-09-02T00:00:00Z"},
    }
    data_file.write_text(json.dumps({"currency": "EUR", "bets": [old_bet]}))
    client = bets_app.create_app(data_file).test_client()
    client.post("/bets", data=bet_form(title="New bet"))
    state = saved(data_file)
    assert state["bets"][0]["positions"][0]["payout"] == "100"
    assert [p["name"] for p in state["players"]] == ["Alice", "Bob"]
    assert [(r["player"], r["profit"]) for r in state["results"]] == [("Alice", "80.00"), ("Bob", "-80.00")]

    # A restart restores exactly what was saved.
    assert bets_app.create_app(data_file).extensions["bets"]["state"] == state

    # An unreadable file is reported and never overwritten.
    data_file.write_text("{ not json")
    broken = bets_app.create_app(data_file).test_client()
    assert broken.get("/").status_code == 500
    assert broken.post("/bets", data=bet_form()).status_code == 500
    assert data_file.read_text() == "{ not json"


def test_players_and_leaderboard(client, data_file):
    def settle(title, result, **form):
        client.post("/bets", data=bet_form(title=title, **form))
        bet_id = next(b["id"] for b in saved(data_file)["bets"] if b["title"] == title)
        client.post(f"/bets/{bet_id}/settlement", data={"result": result})
        return bet_id

    first = settle("One", "yes")                                # Alice +80, Bob −80
    settle("Two", "no", pos_name=["alice", "Cara"])            # Alice −20, Cara +20
    client.post("/bets", data=bet_form(title="Open", pos_name=["Bob", "Dan"]))
    players = lambda: [p["name"] for p in saved(data_file)["players"]]  # noqa: E731
    assert players() == ["Alice", "Bob", "Cara", "Dan"]  # first spelling kept

    # Ranked by net profit: Alice +60, Cara +20, Dan 0, Bob −80.
    board = client.get("/leaderboard").get_data(as_text=True).split("<h2>Wins</h2>")[0]
    order = [board.index(f"{name}</th>") for name in ("Alice", "Cara", "Dan", "Bob")]
    assert order == sorted(order)

    # Players in an open bet can't be deleted; others can, and their history is kept.
    assert client.post("/players/delete", data={"name": "Bob"}).status_code == 409
    assert client.post("/players/delete", data={"name": "cara"}).status_code == 303
    assert players() == ["Alice", "Bob", "Dan"]
    assert "Cara</th>" not in client.get("/leaderboard").get_data(as_text=True).split("<h2>Wins</h2>")[0]

    # Deleting a bet keeps its logged results.
    client.post(f"/bets/{first}/delete")
    assert len(saved(data_file)["results"]) == 4
