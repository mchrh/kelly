from decimal import Decimal

import calc


def position(fmt, value, stake="100", outcome="yes"):
    return {"id": "p", "name": "A", "outcome_id": outcome, "stake": stake, "entry_format": fmt, "entry_value": value}


def test_equivalent_entry_formats_produce_same_payout():
    by_odds = calc.entry_terms(position("decimal_odds", "2.50"))
    by_probability = calc.entry_terms(position("probability", "0.40"))
    assert calc.money(by_odds[3]) == calc.money(by_probability[3]) == Decimal("250.00")
    assert by_odds[1] == Decimal("0.4")
    assert by_probability[0] == Decimal("2.5")


def test_current_valuation():
    view = calc.position_view(position("decimal_odds", "2.50"), {"yes": Decimal("0.6"), "no": Decimal("0.4")}, None)
    assert view["payout"] == Decimal("250.00")
    assert view["value"] == Decimal("150.00")
    assert view["pl"] == Decimal("50.00")
    assert view["profit_if_won"] == Decimal("150.00")


def test_spec_example_second_bettor():
    view = calc.position_view(position("decimal_odds", "2.00", stake="80", outcome="no"),
                              {"yes": Decimal("0.6"), "no": Decimal("0.4")}, None)
    assert view["value"] == Decimal("64.00")
    assert view["pl"] == Decimal("-16.00")


def test_missing_probabilities_leave_values_empty():
    view = calc.position_view(position("decimal_odds", "2.50"), None, None)
    assert view["payout"] == Decimal("250.00")
    assert view["value"] is None and view["pl"] is None and view["probability"] is None


def test_settlement_payouts():
    won = {"result": "winner", "outcome_id": "yes"}
    lost = {"result": "winner", "outcome_id": "no"}
    void = {"result": "void", "outcome_id": None}
    p = position("decimal_odds", "2.50")
    assert calc.position_view(p, None, won)["realized_pl"] == Decimal("150.00")
    assert calc.position_view(p, None, lost)["realized_pl"] == Decimal("-100.00")
    assert calc.position_view(p, None, void)["final_payout"] == Decimal("100.00")
    assert calc.position_view(p, None, void)["realized_pl"] == Decimal("0.00")


def test_money_rounds_half_up():
    # 100 / 0.3 = 333.333…; 1.005 must round up rather than to even.
    assert calc.money(Decimal("1.005")) == Decimal("1.01")
    assert calc.money(calc.entry_terms(position("probability", "0.3"))[3]) == Decimal("333.33")


def binary_position(fmt, value, payout="100", outcome="yes"):
    return {"id": "p", "name": "A", "outcome_id": outcome, "payout": payout, "entry_format": fmt, "entry_value": value}


def test_binary_amount_is_what_the_winner_collects():
    # Yes at 20% on 100 risks 20 to win 80; the other side, No at 80%, risks 80 to win 20.
    yes = calc.position_view(binary_position("probability", "0.2"), None, None)
    no = calc.position_view(binary_position("probability", "0.8", outcome="no"), None, None)
    assert (yes["stake"], yes["payout"], yes["profit_if_won"]) == (Decimal("20.00"), Decimal("100.00"), Decimal("80.00"))
    assert (no["stake"], no["payout"], no["profit_if_won"]) == (Decimal("80.00"), Decimal("100.00"), Decimal("20.00"))
    by_odds = calc.position_view(binary_position("decimal_odds", "5"), None, None)
    assert by_odds["stake"] == Decimal("20.00")


def test_binary_valuation_and_settlement():
    prices = {"yes": Decimal("0.3"), "no": Decimal("0.7")}
    yes = calc.position_view(binary_position("probability", "0.2"), prices, None)
    no = calc.position_view(binary_position("probability", "0.8", outcome="no"), prices, None)
    assert (yes["value"], yes["pl"]) == (Decimal("30.00"), Decimal("10.00"))
    assert (no["value"], no["pl"]) == (Decimal("70.00"), Decimal("-10.00"))
    won = {"result": "winner", "outcome_id": "yes"}
    assert calc.position_view(binary_position("probability", "0.2"), None, won)["realized_pl"] == Decimal("80.00")
    lost = calc.position_view(binary_position("probability", "0.8", outcome="no"), None, won)
    assert (lost["final_payout"], lost["realized_pl"]) == (Decimal("0.00"), Decimal("-80.00"))
    void = {"result": "void", "outcome_id": None}
    assert calc.position_view(binary_position("probability", "0.8"), None, void)["final_payout"] == Decimal("80.00")
