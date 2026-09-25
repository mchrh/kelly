"""Personal betting dashboard: Flask routes, JSON state and form handling."""

import json
import os
import tempfile
import uuid
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, Flask, abort, current_app, jsonify, render_template, request

import calc
import polymarket as pm

DEFAULT_DATA_FILE = Path(__file__).parent / "data" / "bets.json"
STALE_AFTER = timedelta(minutes=2)
TOTAL_TOLERANCE = Decimal("0.01")  # percentage points
HUNDRED = Decimal(100)
BINARY_OUTCOMES = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
DEFAULT_CURRENCY = "EUR"
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$", "AUD": "A$"}

bp = Blueprint("bets", __name__)


class StateError(Exception):
    pass


# --- time and number helpers -------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def plain(value):
    """Decimal to a plain string without exponent or trailing zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_number(text):
    text = (text or "").replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_date(text):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def new_id():
    return str(uuid.uuid4())


# --- persistence ---------------------------------------------------------------

def load_state(path):
    if not path.exists():
        return {"currency": DEFAULT_CURRENCY, "bets": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("bets"), list):
        raise StateError(f'{path} is not a valid bets file (expected an object with a "bets" list).')
    data.setdefault("currency", DEFAULT_CURRENCY)
    for bet in data["bets"]:
        # Binary positions record the amount the winner collects. Files saved before
        # that change stored it under "stake".
        if bet.get("type") == "binary":
            for position in bet.get("positions", []):
                if "payout" not in position and "stake" in position:
                    position["payout"] = position.pop("stake")
    return data


def save_state(path, state):
    """Write to a temporary sibling file, then atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def ctx():
    return current_app.extensions["bets"]


def bets():
    return ctx()["state"]["bets"]


def save():
    save_state(ctx()["path"], ctx()["state"])


def find_bet(bet_id):
    for bet in bets():
        if bet["id"] == bet_id:
            return bet
    abort(404)


# --- derived views ---------------------------------------------------------------

def bet_probabilities(bet):
    if bet["pricing_source"] == "manual":
        source = bet.get("manual_probabilities")
    else:
        source = (bet.get("market_quote") or {}).get("prices")
    return {key: Decimal(value) for key, value in source.items()} if source else None


def pricing_state(bet):
    if bet["pricing_source"] == "manual":
        return "manual"
    quote = bet.get("market_quote")
    if not quote:
        return "none"
    age = datetime.now(timezone.utc) - parse_ts(quote["fetched_at"])
    if bet["id"] in ctx()["refresh"]["failed"] or age > STALE_AFTER:
        return "stale"
    return "live"


def bet_status(bet):
    settlement = bet.get("settlement")
    if settlement:
        return "void" if settlement["result"] == "void" else "settled"
    if bet.get("review"):
        return "review"
    if date.fromisoformat(bet["expiry_date"]) < date.today():
        return "awaiting"
    return "open"


def bet_view(bet):
    probabilities = bet_probabilities(bet)
    labels = {o["id"]: o["label"] for o in bet["outcomes"]}
    settlement = bet.get("settlement")
    return {
        "bet": bet,
        "status": bet_status(bet),
        "pricing": pricing_state(bet),
        "probabilities": [(o, probabilities.get(o["id"]) if probabilities else None) for o in bet["outcomes"]],
        "winner": labels.get(settlement["outcome_id"]) if settlement and settlement["outcome_id"] else None,
        "positions": [
            (p, labels.get(p["outcome_id"], "?"), calc.position_view(p, probabilities, settlement))
            for p in bet["positions"]
        ],
    }


def dashboard_context():
    active = sorted((b for b in bets() if not b.get("settlement")), key=lambda b: (b["expiry_date"], b["created_at"]))
    completed = sorted(
        (b for b in bets() if b.get("settlement")), key=lambda b: b["settlement"]["settled_at"], reverse=True
    )
    return {
        "active": [bet_view(b) for b in active],
        "completed": [bet_view(b) for b in completed],
        "refresh": ctx()["refresh"],
    }


def render_dashboard():
    return render_template("_dashboard.html", **dashboard_context())


# --- forms -------------------------------------------------------------------------

def blank_position():
    return {"id": "", "name": "", "outcome_id": "", "amount": "", "entry_format": "probability", "entry_input": ""}


def entry_input(position):
    value = Decimal(position["entry_value"])
    return plain(value * HUNDRED) if position["entry_format"] == "probability" else plain(value)


def bet_to_form(bet):
    is_mc = bet["type"] == "multiple_choice"
    probs = bet.get("manual_probabilities") or {}
    return {
        "id": bet["id"],
        "locked": bool(bet.get("settlement")),
        "title": bet["title"],
        "type": bet["type"],
        "bet_date": bet["bet_date"],
        "expiry_date": bet["expiry_date"],
        "notes": bet.get("notes", ""),
        "mc_outcomes": [dict(o) for o in bet["outcomes"]] if is_mc else [{"id": new_id(), "label": ""} for _ in range(3)],
        "positions": [dict(p, entry_input=entry_input(p), amount=p.get("payout", p.get("stake")))
                      for p in bet["positions"]],
        "probs": {key: plain(Decimal(value) * HUNDRED) for key, value in probs.items()},
        "polymarket": bet.get("polymarket"),
    }


def new_form():
    return {
        "id": None,
        "locked": False,
        "title": "",
        "type": "binary",
        "bet_date": date.today().isoformat(),
        "expiry_date": "",
        "notes": "",
        "mc_outcomes": [{"id": new_id(), "label": ""} for _ in range(3)],
        "positions": [blank_position(), blank_position()],
        "probs": {},
        "polymarket": None,
    }


def form_from_request(existing):
    f = request.form
    form = bet_to_form(existing) if existing else new_form()
    form.update(
        title=f.get("title", "").strip(),
        bet_date=f.get("bet_date", "").strip(),
        expiry_date=f.get("expiry_date", "").strip(),
        notes=f.get("notes", "").strip(),
    )
    if form["locked"]:
        return form
    form["type"] = "multiple_choice" if f.get("type") == "multiple_choice" else "binary"
    form["mc_outcomes"] = [
        {"id": oid.strip() or new_id(), "label": label.strip()}
        for oid, label in zip(f.getlist("outcome_id"), f.getlist("outcome_label"))
    ]
    form["positions"] = [
        {"id": pid, "name": name.strip(), "outcome_id": outcome, "amount": amount.strip(),
         "entry_format": "probability" if fmt == "probability" else "decimal_odds", "entry_input": value.strip()}
        for pid, name, outcome, amount, fmt, value in zip(
            f.getlist("pos_id"), f.getlist("pos_name"), f.getlist("pos_outcome"),
            f.getlist("pos_amount"), f.getlist("pos_format"), f.getlist("pos_value"),
        )
    ]
    form["probs"] = {key[5:]: value.strip() for key, value in f.items() if key.startswith("prob_")}
    form["polymarket"] = None
    if form["type"] == "binary" and f.get("polymarket_json"):
        with suppress(ValueError):
            form["polymarket"] = json.loads(f["polymarket_json"])
    return form


def form_outcomes(form):
    return BINARY_OUTCOMES if form["type"] == "binary" else form["mc_outcomes"]


def clean_link(candidate):
    """Keep only the metadata we persist from a resolver candidate, or None if malformed."""
    if not isinstance(candidate, dict):
        return None
    tokens = candidate.get("tokens")
    if (
        not isinstance(tokens, dict)
        or set(tokens) != {"yes", "no"}
        or not all(isinstance(t, str) and t for t in tokens.values())
        or not str(candidate.get("url", "")).startswith("https://polymarket.com/event/")
        or not candidate.get("market_id")
    ):
        return None
    return {
        "url": candidate["url"],
        "market_id": str(candidate["market_id"]),
        "condition_id": candidate.get("condition_id"),
        "question": str(candidate.get("question") or ""),
        "tokens": {"yes": tokens["yes"], "no": tokens["no"]},
    }


def parse_distribution(outcomes, probs, bet_type, errors, required):
    """Validate percentage inputs into an outcome-ID -> fraction-string mapping (or None if blank)."""
    if bet_type == "binary":
        raw = probs.get("yes", "")
        if not raw:
            if required:
                errors["probabilities"] = "Enter the Yes probability."
            return None
        yes = parse_number(raw)
        if yes is None or not 0 <= yes <= 100:
            errors["probabilities"] = "Yes probability must be between 0% and 100%."
            return None
        return {"yes": plain(yes / HUNDRED), "no": plain((HUNDRED - yes) / HUNDRED)}
    raw = {o["id"]: probs.get(o["id"], "") for o in outcomes}
    if not any(raw.values()):
        if required:
            errors["probabilities"] = "Enter a probability for every outcome."
        return None
    if not all(raw.values()):
        errors["probabilities"] = "Enter a probability for every outcome, or leave them all blank."
        return None
    values = {key: parse_number(text) for key, text in raw.items()}
    if any(v is None or not 0 <= v <= 100 for v in values.values()):
        errors["probabilities"] = "Each probability must be between 0% and 100%."
        return None
    total = sum(values.values())
    if abs(total - HUNDRED) > TOTAL_TOLERANCE:
        errors["probabilities"] = f"Probabilities total {plain(total)}% — they must add up to 100%."
        return None
    return {key: plain(v / HUNDRED) for key, v in values.items()}


def validate(form):
    """Return (fields, errors). `fields` holds the values to save when there are no errors."""
    errors = {}
    fields = {"title": form["title"], "notes": form["notes"]}
    if not form["title"]:
        errors["title"] = "Enter a title."
    bet_date, expiry = parse_date(form["bet_date"]), parse_date(form["expiry_date"])
    if not bet_date:
        errors["bet_date"] = "Enter the bet date."
    if not expiry:
        errors["expiry_date"] = "Enter the expiry date."
    elif bet_date and expiry < bet_date:
        errors["expiry_date"] = "Expiry must be on or after the bet date."
    fields["bet_date"] = form["bet_date"]
    fields["expiry_date"] = form["expiry_date"]
    if form["locked"]:
        return fields, errors

    outcomes = form_outcomes(form)
    if form["type"] == "multiple_choice":
        labels = [o["label"].casefold() for o in outcomes]
        if len(outcomes) < 3 or not all(labels) or len(set(labels)) != len(labels):
            errors["outcomes"] = "Enter at least three distinct outcomes."
    outcome_ids = {o["id"] for o in outcomes}

    pos_errors = {}
    positions = []
    if len(form["positions"]) < 2:
        errors["positions"] = "Add at least two bettors."
    seen_names = set()
    # Binary bets record the amount the winner collects; multiple choice records the stake.
    amount_key = "payout" if form["type"] == "binary" else "stake"
    for index, row in enumerate(form["positions"]):
        problems = []
        if not row["name"]:
            problems.append("enter a name")
        elif row["name"].casefold() in seen_names:
            problems.append("use a different name from other bettors")
        seen_names.add(row["name"].casefold())
        if row["outcome_id"] not in outcome_ids:
            problems.append("choose a pick")
        amount = parse_number(row["amount"])
        if amount is None or amount <= 0:
            problems.append("enter a positive " + ("bet amount" if amount_key == "payout" else "stake"))
        value = parse_number(row["entry_input"])
        if row["entry_format"] == "decimal_odds":
            if value is None or value <= 1:
                problems.append("decimal odds must be greater than 1")
        elif value is None or not 0 < value < 100:
            problems.append("entry probability must be between 0% and 100%")
            value = None
        if problems:
            message = "; ".join(problems)
            pos_errors[index] = message[0].upper() + message[1:] + "."
            continue
        positions.append({
            "id": row["id"] or new_id(),
            "name": row["name"],
            "outcome_id": row["outcome_id"],
            amount_key: format(amount, "f"),
            "entry_format": row["entry_format"],
            "entry_value": format(value if row["entry_format"] == "decimal_odds" else value / HUNDRED, "f"),
        })
    if pos_errors:
        errors["pos"] = pos_errors

    link = None
    if form["type"] == "binary" and form["polymarket"] is not None:
        link = clean_link(form["polymarket"])
        if link is None:
            errors["polymarket"] = "The Polymarket selection is incomplete. Resolve the link again."
    manual = None if link else parse_distribution(outcomes, form["probs"], form["type"], errors, required=False)

    fields.update(type=form["type"], outcomes=[dict(o) for o in outcomes], positions=positions,
                  polymarket=link, manual_probabilities=manual)
    return fields, errors


def update_quotes(targets, failed, errors):
    """Fetch midpoints for Polymarket-priced bets in one batch. Returns True if any quote changed."""
    tokens = sorted({t for bet in targets for t in bet["polymarket"]["tokens"].values()})
    if not tokens:
        return False
    try:
        midpoints = pm.fetch_midpoints(tokens)
    except pm.PolymarketError as exc:
        errors.append(str(exc))
        failed.update(bet["id"] for bet in targets)
        return False
    changed = False
    stamp = now_iso()
    for bet in targets:
        mapping = bet["polymarket"]["tokens"]
        if all(token in midpoints for token in mapping.values()):
            prices = {oid: plain(midpoints[token]) for oid, token in mapping.items()}
            bet["market_quote"] = {"prices": prices, "fetched_at": stamp}
            changed = True
        else:
            failed.add(bet["id"])
    return changed


def refresh_markets():
    """One refresh cycle: settle confirmed results, then batch-update midpoints."""
    errors, failed, changed = [], set(), False
    open_linked = [b for b in bets() if not b.get("settlement") and b.get("polymarket")]
    if open_linked:
        try:
            markets = pm.fetch_closed_markets(sorted({b["polymarket"]["market_id"] for b in open_linked}))
        except pm.PolymarketError as exc:
            errors.append(str(exc))
        else:
            by_id = {str(m.get("id")): m for m in markets}
            for bet in open_linked:
                market = by_id.get(bet["polymarket"]["market_id"])
                result = pm.final_result(market, bet["polymarket"]["tokens"]) if market else None
                if result and result[0] == "winner":
                    bet["settlement"] = {"result": "winner", "outcome_id": result[1],
                                         "source": "polymarket", "settled_at": now_iso()}
                    bet["review"] = None
                    changed = True
                elif result and (bet.get("review") or {}).get("reason") != result[1]:
                    bet["review"] = {"reason": result[1], "flagged_at": now_iso()}
                    changed = True
    priced = [b for b in bets() if not b.get("settlement") and b.get("polymarket") and b["pricing_source"] == "polymarket"]
    changed = update_quotes(priced, failed, errors) or changed
    if failed and not errors:
        errors.append("Some prices were unavailable.")
    if changed:
        save()
    ctx()["refresh"] = {"at": now_iso(), "errors": errors, "failed": failed}


def refresh_bet_quote(bet):
    """Fetch a quote for a single newly linked or resumed bet, recording failure as stale."""
    failed, errors = set(), []
    changed = update_quotes([bet], failed, errors)
    ctx()["refresh"]["failed"].discard(bet["id"])
    ctx()["refresh"]["failed"].update(failed)
    return changed


def form_response(form, errors, status=200):
    names = sorted({p["name"] for b in bets() for p in b["positions"]}, key=str.casefold)
    return render_template("_bet_form.html", form=form, errors=errors, names=names,
                           outcomes=form_outcomes(form), binary_outcomes=BINARY_OUTCOMES), status


# --- routes -------------------------------------------------------------------------

@bp.before_request
def require_state():
    if ctx()["error"]:
        return render_template("error.html", message=ctx()["error"]), 500


@bp.get("/")
def index():
    return render_template("index.html", **dashboard_context())


@bp.get("/bets/new")
def new_bet():
    return form_response(new_form(), {})


@bp.get("/bets/<bet_id>/edit")
def edit_bet(bet_id):
    return form_response(bet_to_form(find_bet(bet_id)), {})


@bp.post("/bets")
def create_bet():
    form = form_from_request(None)
    fields, errors = validate(form)
    if errors:
        return form_response(form, errors, 422)
    stamp = now_iso()
    bet = {
        "id": new_id(),
        **fields,
        "pricing_source": "polymarket" if fields["polymarket"] else "manual",
        "manual_updated_at": stamp if fields["manual_probabilities"] else None,
        "market_quote": None,
        "settlement": None,
        "review": None,
        "created_at": stamp,
        "updated_at": stamp,
    }
    bets().append(bet)
    if bet["polymarket"]:
        refresh_bet_quote(bet)
    save()
    return render_dashboard()


@bp.post("/bets/<bet_id>/edit")
def update_bet(bet_id):
    bet = find_bet(bet_id)
    form = form_from_request(bet)
    fields, errors = validate(form)
    if errors:
        return form_response(form, errors, 422)
    stamp = now_iso()
    if form["locked"]:
        bet.update(fields)
    else:
        link, manual = fields.pop("polymarket"), fields.pop("manual_probabilities")
        old_link = bet.get("polymarket")
        bet.update(fields)
        if link is None:
            if manual != bet.get("manual_probabilities") or old_link:
                bet["manual_updated_at"] = stamp if manual else None
            bet.update(polymarket=None, market_quote=None, pricing_source="manual",
                       manual_probabilities=manual, review=None)
        elif not old_link or old_link["market_id"] != link["market_id"]:
            bet.update(polymarket=link, market_quote=None, pricing_source="polymarket",
                       manual_probabilities=None, manual_updated_at=None, review=None)
            refresh_bet_quote(bet)
    bet["updated_at"] = stamp
    save()
    return render_dashboard()


@bp.get("/bets/<bet_id>/pricing")
def pricing_form(bet_id):
    bet = find_bet(bet_id)
    return render_template("_pricing_form.html", form=bet_to_form(bet), bet=bet, errors={})


@bp.post("/bets/<bet_id>/pricing")
def update_pricing(bet_id):
    bet = find_bet(bet_id)
    if bet.get("settlement"):
        abort(409)
    if request.form.get("action") == "resume":
        if not bet.get("polymarket"):
            abort(400)
        bet.update(pricing_source="polymarket", manual_probabilities=None, manual_updated_at=None)
        refresh_bet_quote(bet)
    else:
        errors = {}
        probs = {key[5:]: value.strip() for key, value in request.form.items() if key.startswith("prob_")}
        manual = parse_distribution(bet["outcomes"], probs, bet["type"], errors, required=bool(bet.get("polymarket")))
        if errors:
            form = dict(bet_to_form(bet), probs=probs)
            return render_template("_pricing_form.html", form=form, bet=bet, errors=errors), 422
        bet.update(pricing_source="manual", manual_probabilities=manual,
                   manual_updated_at=now_iso() if manual else None)
    bet["updated_at"] = now_iso()
    save()
    return render_dashboard()


@bp.get("/bets/<bet_id>/settlement")
def settlement_form(bet_id):
    return render_template("_settlement_form.html", bet=find_bet(bet_id), error=None)


@bp.post("/bets/<bet_id>/settlement")
def set_settlement(bet_id):
    bet = find_bet(bet_id)
    choice = request.form.get("result", "")
    if choice == "void":
        result = {"result": "void", "outcome_id": None}
    elif choice in {o["id"] for o in bet["outcomes"]}:
        result = {"result": "winner", "outcome_id": choice}
    else:
        return render_template("_settlement_form.html", bet=bet, error="Choose a result."), 422
    bet["settlement"] = {**result, "source": "manual", "settled_at": now_iso()}
    bet["review"] = None
    bet["updated_at"] = now_iso()
    save()
    return render_dashboard()


@bp.post("/bets/<bet_id>/delete")
def delete_bet(bet_id):
    bet = find_bet(bet_id)
    bets().remove(bet)
    ctx()["refresh"]["failed"].discard(bet_id)
    save()
    return render_dashboard()


@bp.post("/polymarket/resolve")
def resolve_link():
    url = (request.get_json(silent=True) or {}).get("url", "")
    try:
        resolved = pm.resolve(str(url))
    except pm.PolymarketError as exc:
        return jsonify(error=str(exc)), 400
    tokens = [t for c in resolved["candidates"] for t in c["tokens"].values()]
    try:
        midpoints = pm.fetch_midpoints(tokens)
    except pm.PolymarketError:
        midpoints = {}
    for candidate in resolved["candidates"]:
        mapping = candidate["tokens"]
        if all(t in midpoints for t in mapping.values()):
            candidate["midpoints"] = {oid: plain(midpoints[t]) for oid, t in mapping.items()}
        else:
            candidate["midpoints"] = None
    return jsonify(resolved)


@bp.post("/refresh")
def refresh():
    refresh_markets()
    return render_dashboard()


# --- template filters and app factory --------------------------------------------------

def currency_symbol():
    code = ctx()["state"].get("currency", DEFAULT_CURRENCY) if ctx()["state"] else DEFAULT_CURRENCY
    return CURRENCY_SYMBOLS.get(code, code + " ")


def fmt_money(value):
    return f"{currency_symbol()}{calc.money(value):,.2f}" if value is not None else "—"


def fmt_signed(value):
    if value is None:
        return "—"
    amount = calc.money(value)
    sign = "+" if amount > 0 else "−" if amount < 0 else ""
    return f"{sign}{currency_symbol()}{abs(amount):,.2f}"


def fmt_pct(value):
    if value is None:
        return "—"
    pct = value * HUNDRED
    if 0 < pct < Decimal("0.1"):
        return "<0.1%"
    return plain(pct.quantize(Decimal("0.1"), rounding=calc.ROUND_HALF_UP)) + "%"


def fmt_odds(value):
    return f"{value.quantize(calc.CENT, rounding=calc.ROUND_HALF_UP)}"


def pl_class(value):
    if value is None or calc.money(value) == 0:
        return ""
    return "pos" if value > 0 else "neg"


def create_app(data_path=None):
    app = Flask(__name__)
    path = Path(data_path or os.environ.get("BETS_FILE") or DEFAULT_DATA_FILE)
    state, error = None, None
    try:
        state = load_state(path)
    except StateError as exc:
        error = str(exc)
    app.extensions["bets"] = {"path": path, "state": state, "error": error,
                              "refresh": {"at": None, "errors": [], "failed": set()}}
    app.jinja_env.filters.update(money=fmt_money, signed=fmt_signed, pct=fmt_pct, odds=fmt_odds,
                                 pl_class=pl_class, plain=plain)
    app.jinja_env.globals.update(currency_symbol=currency_symbol)
    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    application = create_app()
    if application.extensions["bets"]["error"]:
        print(f"Error: {application.extensions['bets']['error']}\nThe file was left untouched.")
    port = int(os.environ.get("PORT", "5050"))
    print(f"Betting dashboard running at http://127.0.0.1:{port}")
    application.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=False)
