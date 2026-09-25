import json

import app as bets_app
from conftest import NO_TOKEN, YES_TOKEN, bet_form, gamma_market, saved


# --- creation and display --------------------------------------------------------------

def test_default_form_is_binary_with_two_bettors(client):
    html = client.get("/bets/new").get_data(as_text=True)
    template_start = html.index("<template")
    assert html[:template_start].count('class="bettor"') == 2
    assert 'value="binary" checked' in html


def test_three_bettors_save_and_display(client, data_file):
    form = bet_form(pos_id=["", "", ""], pos_name=["Alice", "Bob", "Cara"], pos_outcome=["yes", "no", "yes"],
                    pos_stake=["100", "80", "50"], pos_format=["decimal_odds", "decimal_odds", "probability"],
                    pos_value=["2.50", "2.00", "40"], prob_yes="60")
    html = client.post("/bets", data=form).get_data(as_text=True)
    positions = saved(data_file)["bets"][0]["positions"]
    assert [p["name"] for p in positions] == ["Alice", "Bob", "Cara"]
    assert positions[2]["entry_value"] == "0.4"
    assert "Cara" in html and "$125.00" in html  # 50 at 40% pays 125


def test_equivalent_entry_formats_through_form(client):
    form = bet_form(pos_format=["decimal_odds", "probability"], pos_value=["2.50", "40%"],
                    pos_outcome=["yes", "yes"], pos_stake=["100", "100"])
    html = client.post("/bets", data=form).get_data(as_text=True)
    assert html.count("$250.00") == 2


def test_current_valuation_displayed(client):
    html = client.post("/bets", data=bet_form(prob_yes="60")).get_data(as_text=True)
    assert "$150.00" in html and "+$50.00" in html
    assert "$64.00" in html and "−$16.00" in html


def test_missing_prices_show_dash_but_keep_entry_terms(client, data_file):
    html = client.post("/bets", data=bet_form()).get_data(as_text=True)
    assert saved(data_file)["bets"][0]["manual_probabilities"] is None
    assert "2.50" in html and "$250.00" in html
    assert 'data-label="Est. value" class="n">—' in html
    assert 'data-label="Current prob." class="n">—' in html


def test_multiple_choice_uses_each_outcomes_probability(client, data_file):
    form = bet_form(type="multiple_choice", outcome_id=["a", "b", "c"], outcome_label=["Red", "Blue", "Other"],
                    pos_outcome=["a", "b"], pos_value=["2.00", "4.00"], pos_stake=["100", "100"],
                    prob_a="50", prob_b="30", prob_c="20")
    html = client.post("/bets", data=form).get_data(as_text=True)
    bet = saved(data_file)["bets"][0]
    assert [o["label"] for o in bet["outcomes"]] == ["Red", "Blue", "Other"]
    assert bet["manual_probabilities"] == {"a": "0.5", "b": "0.3", "c": "0.2"}
    assert "$100.00" in html      # 200 × 0.5
    assert "$120.00" in html      # 400 × 0.3


def test_multiple_choice_needs_three_distinct_outcomes(client):
    form = bet_form(type="multiple_choice", outcome_id=["a", "b", "c"], outcome_label=["Red", "red", ""],
                    pos_outcome=["a", "b"])
    response = client.post("/bets", data=form)
    assert response.status_code == 422
    assert "three distinct outcomes" in response.get_data(as_text=True)


def test_bad_manual_distribution_is_a_focused_error(client, data_file):
    form = bet_form(type="multiple_choice", outcome_id=["a", "b", "c"], outcome_label=["Red", "Blue", "Other"],
                    pos_outcome=["a", "b"], prob_a="50", prob_b="30", prob_c="10")
    response = client.post("/bets", data=form)
    html = response.get_data(as_text=True)
    assert response.status_code == 422
    assert "total 90% — they must add up to 100%" in html
    assert 'aria-invalid="true" aria-describedby="err-probabilities"' in html
    assert 'value="10"' in html  # entries preserved, not normalized
    assert not data_file.exists()


def test_distribution_within_tolerance_is_accepted_unchanged(client, data_file):
    form = bet_form(type="multiple_choice", outcome_id=["a", "b", "c"], outcome_label=["A", "B", "C"],
                    pos_outcome=["a", "b"], prob_a="33.33", prob_b="33.33", prob_c="33.33")
    assert client.post("/bets", data=form).status_code == 200
    assert saved(data_file)["bets"][0]["manual_probabilities"]["a"] == "0.3333"


def test_input_validation(client):
    form = bet_form(pos_name=["Alice", "alice"], pos_stake=["0", "80"], pos_value=["1", "2"],
                    expiry_date="2026-08-01", title="")
    html = client.post("/bets", data=form).get_data(as_text=True)
    assert "Enter a title." in html
    assert "Expiry must be on or after the bet date." in html
    assert "Enter a positive stake; decimal odds must be greater than 1." in html
    assert "Use a different name from other bettors." in html
    bad_prob = bet_form(pos_format=["probability", "decimal_odds"], pos_value=["100", "2"])
    assert "Entry probability must be between 0% and 100%" in client.post("/bets", data=bad_prob).get_data(as_text=True)
    assert client.post("/bets", data=bet_form(prob_yes="101")).status_code == 422


def test_changing_outcomes_requires_updating_picks(client, data_file):
    client.post("/bets", data=bet_form())
    bet_id = saved(data_file)["bets"][0]["id"]
    form = bet_form(type="multiple_choice", outcome_id=["a", "b", "c"], outcome_label=["A", "B", "C"])
    response = client.post(f"/bets/{bet_id}/edit", data=form)
    assert response.status_code == 422
    assert "Choose a pick." in response.get_data(as_text=True)


# --- Polymarket linking and quotes -------------------------------------------------------

def test_resolve_endpoint_lists_candidates_with_midpoints(client, api):
    api.events["tur-fra"] = {"slug": "tur-fra", "title": "Türkiye vs. France", "markets": [
        gamma_market(1, "tur", "Will Türkiye win?", tokens=("t1", "t2")),
        gamma_market(2, "fra", "Will France win?", tokens=("f1", "f2")),
    ]}
    api.midpoints = {"t1": "0.125", "t2": "0.875", "f1": "0.6"}
    data = client.post("/polymarket/resolve", json={"url": "https://polymarket.com/event/tur-fra"}).get_json()
    assert data["explicit"] is False
    assert data["candidates"][0]["midpoints"] == {"yes": "0.125", "no": "0.875"}
    assert data["candidates"][1]["midpoints"] is None
    bad = client.post("/polymarket/resolve", json={"url": "https://example.com/event/x"})
    assert bad.status_code == 400 and "polymarket.com" in bad.get_json()["error"]


def test_linking_fetches_quote_without_touching_entry_terms(linked_bet):
    assert linked_bet["pricing_source"] == "polymarket"
    assert linked_bet["market_quote"]["prices"] == {"yes": "0.6", "no": "0.4"}
    assert linked_bet["positions"][0]["entry_value"] == "2.50"


def test_reversed_api_outcome_order_maps_prices(client, api, data_file):
    market = gamma_market(77, "rev", "Reversed?", outcomes=("No", "Yes"), tokens=("tok-no", "tok-yes"))
    api.events["rev"] = {"slug": "rev", "title": "Reversed", "markets": [market]}
    api.midpoints = {"tok-no": "0.3", "tok-yes": "0.7"}
    candidate = client.post("/polymarket/resolve", json={"url": "https://polymarket.com/event/rev"}).get_json()["candidates"][0]
    client.post("/bets", data=bet_form(polymarket_json=json.dumps(candidate)))
    bet = saved(data_file)["bets"][0]
    assert bet["polymarket"]["tokens"] == {"yes": "tok-yes", "no": "tok-no"}
    assert bet["market_quote"]["prices"] == {"yes": "0.7", "no": "0.3"}


def test_midpoints_used_without_complement_or_normalization(client, api, linked_bet, data_file):
    api.midpoints = {YES_TOKEN: "0.62", NO_TOKEN: "0.41"}
    html = client.post("/refresh").get_data(as_text=True)
    assert saved(data_file)["bets"][0]["market_quote"]["prices"] == {"yes": "0.62", "no": "0.41"}
    assert "62%" in html and "41%" in html
    midpoint_calls = [c for c in api.calls if c[0] == "POST"]
    assert midpoint_calls[-1][2] == [{"token_id": t} for t in sorted([YES_TOKEN, NO_TOKEN])]


def test_refresh_batches_tokens_across_bets(client, api, linked_bet):
    api.markets["501"] = gamma_market(501, "other", "Other?", tokens=("o-yes", "o-no"))
    api.midpoints.update({"o-yes": "0.2", "o-no": "0.8"})
    link = {"url": "https://polymarket.com/event/other", "market_id": "501", "question": "Other?",
            "tokens": {"yes": "o-yes", "no": "o-no"}}
    client.post("/bets", data=bet_form(polymarket_json=json.dumps(link)))
    api.calls.clear()
    client.post("/refresh")
    posts = [c for c in api.calls if c[0] == "POST"]
    gets = [c for c in api.calls if c[0] == "GET"]
    assert len(posts) == 1 and len(posts[0][2]) == 4
    assert len(gets) == 1 and [v for k, v in gets[0][2] if k == "id"] == ["500", "501"]


def test_partial_quote_keeps_previous_snapshot_and_marks_stale(client, api, linked_bet, data_file):
    api.midpoints = {YES_TOKEN: "0.9"}
    html = client.post("/refresh").get_data(as_text=True)
    bet = saved(data_file)["bets"][0]
    assert bet["market_quote"] == linked_bet["market_quote"]
    assert "Polymarket · stale" in html and "Last quote" in html
    assert "Some prices were unavailable." in html


def test_old_quote_is_stale(app, client, linked_bet, data_file):
    state = app.extensions["bets"]["state"]
    state["bets"][0]["market_quote"]["fetched_at"] = "2020-01-01T00:00:00Z"
    html = client.get("/").get_data(as_text=True)
    assert "Polymarket · stale" in html


def test_changing_link_clears_mapping_and_quote(client, api, linked_bet, data_file):
    api.midpoints = {}
    link = {"url": "https://polymarket.com/event/new", "market_id": "900", "question": "New?",
            "tokens": {"yes": "n-yes", "no": "n-no"}}
    client.post(f"/bets/{linked_bet['id']}/edit", data=bet_form(polymarket_json=json.dumps(link)))
    bet = saved(data_file)["bets"][0]
    assert bet["polymarket"]["market_id"] == "900"
    assert bet["market_quote"] is None


def test_unlinking_switches_to_manual(client, linked_bet, data_file):
    client.post(f"/bets/{linked_bet['id']}/edit", data=bet_form(prob_yes="55"))
    bet = saved(data_file)["bets"][0]
    assert bet["polymarket"] is None and bet["market_quote"] is None
    assert bet["pricing_source"] == "manual"
    assert bet["manual_probabilities"] == {"yes": "0.55", "no": "0.45"}


# --- manual pricing override ------------------------------------------------------------

def test_manual_override_survives_refresh_but_settlement_still_checked(client, api, linked_bet, data_file):
    bet_id = linked_bet["id"]
    client.post(f"/bets/{bet_id}/pricing", data={"action": "manual", "prob_yes": "30"})
    api.midpoints = {YES_TOKEN: "0.9", NO_TOKEN: "0.1"}
    html = client.post("/refresh").get_data(as_text=True)
    bet = saved(data_file)["bets"][0]
    assert bet["pricing_source"] == "manual"
    assert bet["manual_probabilities"] == {"yes": "0.3", "no": "0.7"}
    assert bet["market_quote"]["prices"] == {"yes": "0.6", "no": "0.4"}  # not refreshed while overridden
    assert '<span class="badge quiet">Manual</span>' in html
    assert any(c[0] == "GET" and c[1].endswith("/markets") for c in api.calls)

    api.markets["500"].update(closed=True, umaResolutionStatus="resolved", outcomePrices='["1", "0"]')
    client.post("/refresh")
    assert saved(data_file)["bets"][0]["settlement"]["outcome_id"] == "yes"


def test_resume_polymarket_pricing(client, api, linked_bet, data_file):
    bet_id = linked_bet["id"]
    client.post(f"/bets/{bet_id}/pricing", data={"action": "manual", "prob_yes": "30"})
    api.midpoints = {YES_TOKEN: "0.55", NO_TOKEN: "0.46"}
    client.post(f"/bets/{bet_id}/pricing", data={"action": "resume"})
    bet = saved(data_file)["bets"][0]
    assert bet["pricing_source"] == "polymarket" and bet["manual_probabilities"] is None
    assert bet["market_quote"]["prices"] == {"yes": "0.55", "no": "0.46"}


def test_resume_with_failed_fetch_shows_stored_quote_as_stale(client, api, linked_bet):
    bet_id = linked_bet["id"]
    client.post(f"/bets/{bet_id}/pricing", data={"action": "manual", "prob_yes": "30"})
    api.fail_midpoints = True
    html = client.post(f"/bets/{bet_id}/pricing", data={"action": "resume"}).get_data(as_text=True)
    assert "Polymarket · stale" in html and "60%" in html


def test_linked_override_requires_complete_probabilities(client, linked_bet):
    response = client.post(f"/bets/{linked_bet['id']}/pricing", data={"action": "manual", "prob_yes": ""})
    assert response.status_code == 422


# --- settlement ------------------------------------------------------------

def resolve_market(api, prices):
    api.markets["500"].update(closed=True, umaResolutionStatus="resolved", outcomePrices=json.dumps(prices))


def test_confirmed_result_settles_once(client, api, linked_bet, data_file):
    resolve_market(api, ["0", "1"])
    html = client.post("/refresh").get_data(as_text=True)
    settlement = saved(data_file)["bets"][0]["settlement"]
    assert settlement["result"] == "winner" and settlement["outcome_id"] == "no"
    assert settlement["source"] == "polymarket"
    assert "Result: <strong>No</strong>" in html and "+$80.00" in html and "−$100.00" in html
    api.calls.clear()
    client.post("/refresh")
    assert saved(data_file)["bets"][0]["settlement"] == settlement
    assert api.calls == []  # settled bets are no longer polled


def test_unconfirmed_results_remain_open(app, client, api, linked_bet, data_file):
    state = app.extensions["bets"]["state"]
    state["bets"][0]["expiry_date"] = "2020-01-01"
    api.markets["500"].update(closed=True, outcomePrices='["1", "0"]')  # closed, no final resolution
    html = client.post("/refresh").get_data(as_text=True)
    assert saved(data_file)["bets"][0]["settlement"] is None
    assert "Awaiting result" in html
    api.markets["500"]["umaResolutionStatus"] = "proposed"
    client.post("/refresh")
    assert saved(data_file)["bets"][0]["settlement"] is None


def test_fifty_fifty_result_needs_review(client, api, linked_bet, data_file):
    resolve_market(api, ["0.5", "0.5"])
    html = client.post("/refresh").get_data(as_text=True)
    bet = saved(data_file)["bets"][0]
    assert bet["settlement"] is None and bet["review"]
    assert "Needs result review" in html
    client.post(f"/bets/{bet['id']}/settlement", data={"result": "void"})
    bet = saved(data_file)["bets"][0]
    assert bet["settlement"]["result"] == "void" and bet["review"] is None


def test_manual_correction_is_preserved(client, api, linked_bet, data_file):
    resolve_market(api, ["1", "0"])
    client.post("/refresh")
    client.post(f"/bets/{linked_bet['id']}/settlement", data={"result": "no"})
    client.post("/refresh")
    settlement = saved(data_file)["bets"][0]["settlement"]
    assert settlement["outcome_id"] == "no" and settlement["source"] == "manual"


def test_manual_settlement_and_void(client, data_file):
    client.post("/bets", data=bet_form())
    bet_id = saved(data_file)["bets"][0]["id"]
    assert client.post(f"/bets/{bet_id}/settlement", data={"result": "bogus"}).status_code == 422
    html = client.post(f"/bets/{bet_id}/settlement", data={"result": "void"}).get_data(as_text=True)
    assert "Void — stakes refunded" in html and "Refunded" in html


def test_completed_bet_only_allows_metadata_edits(client, data_file):
    client.post("/bets", data=bet_form())
    bet_id = saved(data_file)["bets"][0]["id"]
    client.post(f"/bets/{bet_id}/settlement", data={"result": "yes"})
    form = bet_form(title="Renamed", pos_stake=["999", "999"])
    assert client.post(f"/bets/{bet_id}/edit", data=form).status_code == 200
    bet = saved(data_file)["bets"][0]
    assert bet["title"] == "Renamed"
    assert [p["stake"] for p in bet["positions"]] == ["100", "80"]  # as entered


def test_metadata_failure_does_not_block_midpoints(client, api, linked_bet, data_file):
    api.fail_markets = True
    api.midpoints = {YES_TOKEN: "0.7", NO_TOKEN: "0.31"}
    html = client.post("/refresh").get_data(as_text=True)
    assert saved(data_file)["bets"][0]["market_quote"]["prices"] == {"yes": "0.7", "no": "0.31"}
    assert 'data-errors="Polymarket returned HTTP 503."' in html


def test_midpoint_failure_does_not_discard_result(client, api, linked_bet, data_file):
    resolve_market(api, ["1", "0"])
    api.fail_midpoints = True
    client.post("/refresh")
    assert saved(data_file)["bets"][0]["settlement"]["outcome_id"] == "yes"


# --- persistence ------------------------------------------------------------

def test_restart_restores_everything(client, api, linked_bet, data_file):
    client.post("/bets", data=bet_form(title="Manual one", prob_yes="45"))
    resolve_market(api, ["1", "0"])
    client.post("/refresh")
    before = saved(data_file)
    restarted = bets_app.create_app(data_file)
    assert restarted.extensions["bets"]["state"] == before
    html = restarted.test_client().get("/").get_data(as_text=True)
    assert "Manual one" in html and "Result: <strong>Yes</strong>" in html


def test_delete_bet(client, data_file):
    client.post("/bets", data=bet_form())
    bet_id = saved(data_file)["bets"][0]["id"]
    client.post(f"/bets/{bet_id}/delete")
    assert saved(data_file)["bets"] == []


def test_invalid_file_is_reported_and_not_overwritten(data_file, api):
    data_file.parent.mkdir(parents=True)
    data_file.write_text("{ not json")
    broken = bets_app.create_app(data_file)
    client = broken.test_client()
    response = client.get("/")
    assert response.status_code == 500
    assert "could not be loaded" in response.get_data(as_text=True)
    assert client.post("/bets", data=bet_form()).status_code == 500
    assert client.post("/refresh").status_code == 500
    assert data_file.read_text() == "{ not json"


def test_missing_file_starts_empty(client, data_file):
    html = client.get("/").get_data(as_text=True)
    assert "No active bets" in html
    assert not data_file.exists()


def test_saves_are_atomic(client, data_file):
    client.post("/bets", data=bet_form())
    assert [p.name for p in data_file.parent.iterdir()] == ["bets.json"]


def test_user_text_is_escaped(client):
    html = client.post("/bets", data=bet_form(title="<script>x</script>")).get_data(as_text=True)
    assert "<script>x" not in html and "&lt;script&gt;" in html
