"""Position valuation and settlement arithmetic. All values are Decimal."""

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ONE = Decimal(1)


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def entry_terms(position):
    """Return (decimal_odds, entry_probability, stake, gross_win_payout) for a position.

    Binary positions record `payout`, the amount the winner collects; the stake at
    risk is that amount's share at the entry probability (Yes at 20% on 100 risks 20).
    Other positions record `stake`, and the payout follows from the odds.
    """
    value = Decimal(position["entry_value"])
    is_odds = position["entry_format"] == "decimal_odds"
    odds, probability = (value, ONE / value) if is_odds else (ONE / value, value)
    if "payout" in position:
        payout = Decimal(position["payout"])
        return odds, probability, (payout / value if is_odds else payout * value), payout
    stake = Decimal(position["stake"])
    return odds, probability, stake, (stake * value if is_odds else stake / value)


def position_view(position, probabilities, settlement):
    """Derived display values for one position.

    `probabilities` maps outcome IDs to Decimal (or is None when no pricing exists).
    """
    odds, entry_prob, stake, payout = entry_terms(position)
    view = {
        "stake": money(stake),
        "odds": odds,
        "entry_probability": entry_prob,
        "payout": money(payout),
        "profit_if_won": money(payout) - money(stake),
        "probability": None,
        "value": None,
        "pl": None,
        "result": None,
        "final_payout": None,
        "realized_pl": None,
    }
    if settlement:
        final = settlement_payout(position, settlement)
        view["final_payout"] = final
        view["realized_pl"] = final - money(stake)
        if settlement["result"] == "void":
            view["result"] = "void"
        elif settlement["outcome_id"] == position["outcome_id"]:
            view["result"] = "won"
        else:
            view["result"] = "lost"
        return view
    p = probabilities.get(position["outcome_id"]) if probabilities else None
    if p is not None:
        value = payout * p
        view["probability"] = p
        view["value"] = money(value)
        view["pl"] = money(value - stake)
    return view


def settlement_payout(position, settlement):
    _, _, stake, payout = entry_terms(position)
    if settlement["result"] == "void":
        return money(stake)
    if settlement["outcome_id"] == position["outcome_id"]:
        return money(payout)
    return money(Decimal(0))
