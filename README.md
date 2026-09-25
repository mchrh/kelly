<p align="center"><img src="static/logo.svg" alt="Kelly" width="220"></p>

# Kelly

A personal dashboard for tracking private bets between friends. It records each person's stake and agreed odds, estimates what each position is currently worth, and settles bets manually or automatically from a linked Polymarket market.

It runs locally and keeps everything in one JSON file. There are no accounts and no database.

> **Your bets live in `data/bets.json`.** That file is the only record of your bets, and it is deliberately not tracked by git. Keep it in the `data/` directory, and back it up or copy it across when you move or re-clone the project. If it's missing, the app starts with an empty dashboard and creates a new file the first time you save a bet.

## Install

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python app.py
```

Open <http://127.0.0.1:5050>. The server listens only on `127.0.0.1`. Set `PORT` to use a different port.

## Data file

Bets are saved to `data/bets.json`. Set `BETS_FILE=/path/to/bets.json` to use a different file. Each save writes a temporary file and then replaces the original, so an interrupted write never leaves a half-written file.

If the file is missing, the dashboard starts empty. If the file exists but can't be read, the app shows an error and doesn't write anything, so the file isn't overwritten.

## Currency

All amounts use a single display currency, set by the `currency` field at the top of `data/bets.json`. It defaults to euros (`EUR`). To use a different currency:

```json
{ "currency": "USD", "bets": [ ... ] }
```

`USD`, `EUR`, `GBP`, `JPY`, `CAD` and `AUD` show a symbol. Any other code is shown as a prefix, for example `CHF 12.00`. Change the value while the app is stopped, then start it again.

## Players and leaderboard

- **Players**: anyone who places a bet is added to the players list. Their names are suggested when you add a bettor. You can delete a player on the **Players** page once they're no longer in an open bet. That removes them from suggestions and from the leaderboard, but keeps their past results; if they bet again, they're added back with their history.
- **Results log**: when a bet settles, manually or from Polymarket, each bettor's result is written to a log in `data/bets.json`. Correcting a result replaces that bet's entries. Deleting a bet keeps its logged results.
- **Leaderboard**: ranks players by net profit across the log, with bets settled, wins, win rate and amount staked. Void bets are left out. Below it is a list of wins, newest first.

## Automatic updates

While the dashboard is open in a browser tab, it refreshes every 60 seconds. It pauses while the tab is hidden and catches up when you return. **Refresh** runs an update immediately. Each update:

1. Checks linked, unsettled markets for a confirmed final result. A bet settles only when Polymarket reports `closed`, `umaResolutionStatus == "resolved"` and final prices of exactly 1 and 0. A finalized result with any other prices, such as 50/50, is marked **Needs result review** for you to settle yourself.
2. Fetches Polymarket midpoints for both Yes and No in one batch request.
3. Saves any changes and redraws the dashboard. An open add/edit drawer is left alone.

There is no background process. **Updates happen only while the dashboard is open.** Closing the browser tab stops them, and reopening the dashboard catches up.

## Tests

```bash
.venv/bin/python -m pytest
```

The tests use recorded-style Polymarket responses and don't need network access.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | Flask routes, JSON load/save, form handling, refresh cycle |
| `polymarket.py` | Link resolution, market metadata, midpoints, settlement rule |
| `calc.py` | Payout, valuation and settlement arithmetic (`Decimal`) |
| `templates/` | Page, dashboard partial, card and drawer forms |
| `static/` | `style.css` and `app.js` |
| `tests/` | pytest suite |
