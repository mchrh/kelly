from decimal import Decimal

import calc


def position(fmt, value, stake="100", outcome="yes"):
    return {"id": "p", "name": "A", "outcome_id": outcome, "stake": stake, "entry_format": fmt, "entry_value": value}


def test_equivalent_entry_formats_produce_same_payout():
    by_odds = calc.entry_terms(position("decimal_odds", "2.50"))
    by_probability = calc.entry_terms(position("probability", "0.40"))
    assert calc.money(by_odds[2]) == calc.money(by_probability[2]) == Decimal("250.00")
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
    assert calc.money(calc.entry_terms(position("probability", "0.3"))[2]) == Decimal("333.33")
