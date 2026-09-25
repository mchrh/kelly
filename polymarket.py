"""Polymarket link resolution, market metadata and midpoint requests.

Requests only ever go to the fixed API hosts below, never to a pasted address.
"""

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TIMEOUT = 10


class PolymarketError(Exception):
    """A user-facing problem with a link or an API request."""


def parse_url(url):
    """Return (event_slug, market_slug or None) from a polymarket.com event URL."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in ("polymarket.com", "www.polymarket.com"):
        raise PolymarketError("Paste a polymarket.com event link.")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) not in (2, 3) or segments[0] != "event":
        raise PolymarketError("Expected a link like polymarket.com/event/… .")
    return segments[1], (segments[2] if len(segments) == 3 else None)


def _get(path, params=None):
    try:
        response = requests.get(GAMMA + path, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise PolymarketError(f"Polymarket request failed: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        raise PolymarketError("Polymarket could not find that event.")
    if not response.ok:
        raise PolymarketError(f"Polymarket returned HTTP {response.status_code}.")
    return response.json()


def _json_list(value):
    """Gamma encodes outcomes, token IDs and prices as JSON strings of arrays."""
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def binary_token_map(market):
    """Map the app's yes/no outcome IDs to CLOB token IDs by label, or None if not Yes/No."""
    labels = [str(label).strip().lower() for label in _json_list(market.get("outcomes"))]
    tokens = [str(token) for token in _json_list(market.get("clobTokenIds"))]
    if sorted(labels) != ["no", "yes"] or len(tokens) != 2:
        return None
    return {label: token for label, token in zip(labels, tokens)}


def market_url(event_slug, market_slug):
    if market_slug and market_slug != event_slug:
        return f"https://polymarket.com/event/{event_slug}/{market_slug}"
    return f"https://polymarket.com/event/{event_slug}"


def _candidate(event_slug, market):
    return {
        "url": market_url(event_slug, market.get("slug")),
        "market_id": str(market["id"]),
        "condition_id": market.get("conditionId"),
        "question": market.get("question") or "",
        "tokens": binary_token_map(market),
        "labels": _json_list(market.get("outcomes")),
        "end_date": (market.get("endDateIso") or market.get("endDate") or "")[:10] or None,
        "closed": bool(market.get("closed")),
    }


def resolve(url):
    """Resolve a pasted link to its compatible Yes/No market candidates.

    Returns {"event_title", "candidates", "explicit"}; `explicit` is True when the
    URL named a specific child market.
    """
    event_slug, market_slug = parse_url(url)
    event = _get(f"/events/slug/{event_slug}")
    markets = event.get("markets") or []
    if market_slug:
        match = [m for m in markets if m.get("slug") == market_slug]
        if not match:
            raise PolymarketError("That market was not found in the event.")
        candidate = _candidate(event_slug, match[0])
        if candidate["tokens"] is None:
            raise PolymarketError(
                f"“{candidate['question']}” is not a Yes/No market, so it cannot price a binary bet."
            )
        return {"event_title": event.get("title"), "candidates": [candidate], "explicit": True}
    candidates = [c for c in (_candidate(event_slug, m) for m in markets) if c["tokens"]]
    if not candidates:
        raise PolymarketError("This event has no Yes/No markets, so it cannot supply binary pricing.")
    return {"event_title": event.get("title"), "candidates": candidates, "explicit": False}


def fetch_closed_markets(market_ids):
    """Fetch metadata for the given market IDs that Polymarket reports as closed.

    Gamma's ID filter omits closed markets unless `closed=true` is passed, and only
    closed markets can settle, so that is the only set requested.
    """
    if not market_ids:
        return []
    params = [("id", market_id) for market_id in market_ids]
    params += [("closed", "true"), ("limit", str(len(market_ids)))]
    data = _get("/markets", params)
    if not isinstance(data, list):
        raise PolymarketError("Unexpected market metadata response.")
    return data


def final_result(market, tokens):
    """Classify a market's final state for automatic settlement.

    Returns ("winner", outcome_id), ("review", reason) or None (not finalized).
    """
    if market.get("closed") is not True or market.get("umaResolutionStatus") != "resolved":
        return None
    try:
        prices = [Decimal(str(p)) for p in _json_list(market.get("outcomePrices"))]
    except InvalidOperation:
        prices = []
    market_tokens = [str(t) for t in _json_list(market.get("clobTokenIds"))]
    if len(prices) != 2 or sorted(prices) != [0, 1] or len(market_tokens) != 2:
        shown = " / ".join(str(p) for p in prices) or "unknown"
        return ("review", f"Polymarket resolved this market without a single winner ({shown}).")
    winning_token = market_tokens[prices.index(Decimal(1))]
    for outcome_id, token in tokens.items():
        if token == winning_token:
            return ("winner", outcome_id)
    return ("review", "Polymarket's winning outcome does not match the saved mapping.")


def fetch_midpoints(token_ids):
    """Return {token_id: Decimal midpoint} for the tokens Polymarket priced."""
    if not token_ids:
        return {}
    body = [{"token_id": token} for token in token_ids]
    try:
        response = requests.post(CLOB + "/midpoints", json=body, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise PolymarketError(f"Price request failed: {exc.__class__.__name__}") from exc
    if not response.ok:
        raise PolymarketError(f"Price request returned HTTP {response.status_code}.")
    data = response.json()
    if not isinstance(data, dict):
        raise PolymarketError("Unexpected price response.")
    midpoints = {}
    for token, value in data.items():
        try:
            price = Decimal(str(value))
        except InvalidOperation:
            continue
        if price.is_finite() and 0 <= price <= 1:
            midpoints[str(token)] = price
    return midpoints
