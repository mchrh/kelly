import pytest

import polymarket
from conftest import gamma_market


def test_parse_url_variants():
    assert polymarket.parse_url("https://polymarket.com/event/foo") == ("foo", None)
    assert polymarket.parse_url("https://www.polymarket.com/event/foo/bar?tid=1#x") == ("foo", "bar")
    for bad in ("https://evil.com/event/foo", "https://polymarket.com/markets/foo", "ftp://polymarket.com/event/x"):
        with pytest.raises(polymarket.PolymarketError):
            polymarket.parse_url(bad)


def event(slug, markets):
    return {"id": "1", "slug": slug, "title": "Türkiye vs. France", "markets": markets}


def test_multi_market_event_requires_selection(api):
    api.events["tur-fra"] = event("tur-fra", [
        gamma_market(1, "tur-fra-tur", "Will Türkiye win?"),
        gamma_market(2, "tur-fra-draw", "Will it end in a draw?"),
        gamma_market(3, "tur-fra-fra", "Will France win?"),
        gamma_market(4, "tur-fra-score", "Exact score", outcomes=("1-0", "2-1")),
    ])
    result = polymarket.resolve("https://polymarket.com/event/tur-fra")
    assert result["explicit"] is False
    assert [c["question"] for c in result["candidates"]] == [
        "Will Türkiye win?", "Will it end in a draw?", "Will France win?"]


def test_child_market_url_selects_that_market(api):
    api.events["tur-fra"] = event("tur-fra", [
        gamma_market(1, "tur-fra-tur", "Will Türkiye win?"),
        gamma_market(3, "tur-fra-fra", "Will France win?"),
    ])
    result = polymarket.resolve("https://polymarket.com/event/tur-fra/tur-fra-fra")
    assert result["explicit"] is True
    assert [c["market_id"] for c in result["candidates"]] == ["3"]
    assert result["candidates"][0]["url"] == "https://polymarket.com/event/tur-fra/tur-fra-fra"


def test_incompatible_child_market_is_rejected(api):
    api.events["dota"] = event("dota", [gamma_market(9, "game1", "Game 1 Winner", outcomes=("Xtreme", "LGD"))])
    with pytest.raises(polymarket.PolymarketError, match="not a Yes/No market"):
        polymarket.resolve("https://polymarket.com/event/dota/game1")
    with pytest.raises(polymarket.PolymarketError, match="no Yes/No markets"):
        polymarket.resolve("https://polymarket.com/event/dota")


def test_reversed_outcome_order_maps_tokens_by_label():
    market = gamma_market(7, "m", "Q?", outcomes=("No", "Yes"), tokens=("tok-no", "tok-yes"))
    assert polymarket.binary_token_map(market) == {"no": "tok-no", "yes": "tok-yes"}


@pytest.mark.parametrize("fields", [
    {"closed": False, "uma": None, "prices": ("0.999", "0.001")},   # near 100%, still trading
    {"closed": True, "uma": None, "prices": ("1", "0")},             # closed only
    {"closed": True, "uma": "proposed", "prices": ("1", "0")},       # proposal not final
    {"closed": True, "uma": "disputed", "prices": ("1", "0")},
    {"closed": False, "uma": "resolved", "prices": ("1", "0")},      # not closed
])
def test_unconfirmed_results_do_not_settle(fields):
    market = gamma_market(1, "m", "Q?", tokens=("a", "b"), **fields)
    assert polymarket.final_result(market, {"yes": "a", "no": "b"}) is None


def test_confirmed_result_maps_winner_through_saved_tokens():
    market = gamma_market(1, "m", "Q?", outcomes=("No", "Yes"), tokens=("b", "a"),
                          prices=("0", "1"), closed=True, uma="resolved")
    assert polymarket.final_result(market, {"yes": "a", "no": "b"}) == ("winner", "yes")


def test_fifty_fifty_requires_review():
    market = gamma_market(1, "m", "Q?", tokens=("a", "b"), prices=("0.5", "0.5"), closed=True, uma="resolved")
    kind, reason = polymarket.final_result(market, {"yes": "a", "no": "b"})
    assert kind == "review" and "0.5 / 0.5" in reason
