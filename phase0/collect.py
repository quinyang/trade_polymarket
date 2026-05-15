"""Phase 0 data collector for the favorite-longshot bias study.

Produces phase0/data/events.csv with one row per selection (the two sides of a
match are two rows). Schema:

    event_id        stable id for the match
    sport           sport key
    commence_time   ISO8601 event start time
    selection       label for this side (home/away or fav/dog)
    opening_price   market-implied probability when first observed
    closing_price   market-implied probability just before commence
    outcome         1 if this selection won, else 0
    data_source     'synthetic' or 'odds-api'

Three subcommands:

    synthetic   Generate a simulated dataset with a known favorite-longshot
                bias baked in. Runs offline, no API key. Use this to verify
                the analysis pipeline end-to-end. Synthetic results PROVE
                NOTHING about real edge.

    snapshot    Append the current h2h odds for a sport to data/snapshots.csv.
                Free tier of The Odds API works. Run repeatedly over days
                (e.g. cron) to accumulate opening vs closing prices.

    build       Join accumulated snapshots + final scores into events.csv.

The Odds API key is read from the ODDS_API_KEY environment variable.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from oddslib import american_to_prob

DATA_DIR = Path(__file__).parent / "data"
EVENTS_CSV = DATA_DIR / "events.csv"
SNAPSHOTS_CSV = DATA_DIR / "snapshots.csv"

EVENT_FIELDS = [
    "event_id",
    "sport",
    "commence_time",
    "selection",
    "opening_price",
    "closing_price",
    "outcome",
    "data_source",
]


# --------------------------------------------------------------------------
# Synthetic generator
# --------------------------------------------------------------------------

def generate_synthetic(n_events: int, bias: float, vig: float, seed: int) -> list[dict]:
    """Generate `n_events` matches with an embedded favorite-longshot bias.

    For each match:
      * draw a true favorite win probability `t` in [0.5, 0.95]
      * the FAIR market underprices the favorite and overprices the longshot
        (the favorite-longshot bias): the displayed implied probability is
        compressed toward 0.5 by `bias`
      * vig is then added on top of the compressed fair probabilities
      * the opening price carries the full bias; the closing price has the
        bias partly corrected (markets sharpen toward commence)
      * the outcome is drawn from the TRUE probability `t`
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    for i in range(n_events):
        t = float(rng.uniform(0.50, 0.95))  # true favorite win prob

        # Favorite-longshot bias: compress displayed prob toward 0.5.
        # Opening carries full bias, closing carries ~40% of it (correction).
        fav_open_fair = t - bias * (t - 0.5)
        fav_close_fair = t - 0.40 * bias * (t - 0.5)
        dog_open_fair = 1.0 - fav_open_fair
        dog_close_fair = 1.0 - fav_close_fair

        # Add vig (overround) to get displayed market prices.
        fav_open = fav_open_fair * vig
        dog_open = dog_open_fair * vig
        fav_close = fav_close_fair * (1.0 + (vig - 1.0) * 0.7)  # vig tightens
        dog_close = dog_close_fair * (1.0 + (vig - 1.0) * 0.7)

        favorite_won = int(rng.random() < t)

        commence = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        rows.append({
            "event_id": f"syn-{i:05d}",
            "sport": "synthetic_league",
            "commence_time": commence,
            "selection": "fav",
            "opening_price": round(fav_open, 4),
            "closing_price": round(fav_close, 4),
            "outcome": favorite_won,
            "data_source": "synthetic",
        })
        rows.append({
            "event_id": f"syn-{i:05d}",
            "sport": "synthetic_league",
            "commence_time": commence,
            "selection": "dog",
            "opening_price": round(dog_open, 4),
            "closing_price": round(dog_close, 4),
            "outcome": 1 - favorite_won,
            "data_source": "synthetic",
        })
    return rows


def cmd_synthetic(args: argparse.Namespace) -> int:
    rows = generate_synthetic(args.events, args.bias, args.vig, args.seed)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} selection rows ({args.events} events) -> {EVENTS_CSV}")
    print("NOTE: synthetic data is a PIPELINE TEST ONLY. It proves nothing")
    print("      about a real tradable edge. Collect real data before trusting")
    print("      any 'PROCEED' verdict.")
    return 0


# --------------------------------------------------------------------------
# The Odds API: live snapshot
# --------------------------------------------------------------------------

def _require_requests():
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.exit("the 'requests' package is required for odds-api commands: "
                 "pip install requests")
    import requests
    return requests


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Append current h2h odds for a sport to snapshots.csv."""
    requests = _require_requests()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("set the ODDS_API_KEY environment variable first")

    url = f"https://api.the-odds-api.com/v4/sports/{args.sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": args.regions,
        "markets": "h2h",
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    games = resp.json()
    fetched_at = datetime.now(timezone.utc).isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not SNAPSHOTS_CSV.exists()
    fields = ["fetched_at", "event_id", "sport", "commence_time",
              "selection", "implied_prob"]
    n = 0
    with SNAPSHOTS_CSV.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for g in games:
            # Average the implied prob for each outcome across all bookmakers.
            acc: dict[str, list[float]] = {}
            for bk in g.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    for oc in mkt.get("outcomes", []):
                        acc.setdefault(oc["name"], []).append(
                            american_to_prob(oc["price"]))
            for name, probs in acc.items():
                writer.writerow({
                    "fetched_at": fetched_at,
                    "event_id": g["id"],
                    "sport": g["sport_key"],
                    "commence_time": g["commence_time"],
                    "selection": name,
                    "implied_prob": round(sum(probs) / len(probs), 4),
                })
                n += 1
    print(f"appended {n} rows -> {SNAPSHOTS_CSV} (remaining API quota in "
          f"response header x-requests-remaining: "
          f"{resp.headers.get('x-requests-remaining', '?')})")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Join snapshots + final scores into events.csv.

    opening_price = earliest snapshot per (event_id, selection)
    closing_price = latest snapshot before commence_time
    outcome       = from The Odds API /scores endpoint
    """
    requests = _require_requests()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("set the ODDS_API_KEY environment variable first")
    if not SNAPSHOTS_CSV.exists():
        sys.exit(f"no snapshots found at {SNAPSHOTS_CSV}; run 'snapshot' first")

    # Group snapshots by (event_id, selection).
    snaps: dict[tuple[str, str], list[dict]] = {}
    sports = set()
    with SNAPSHOTS_CSV.open() as fh:
        for row in csv.DictReader(fh):
            key = (row["event_id"], row["selection"])
            snaps.setdefault(key, []).append(row)
            sports.add(row["sport"])

    # Fetch final scores for every sport seen.
    results: dict[str, dict] = {}
    for sport in sports:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/scores"
        resp = requests.get(url, params={"apiKey": api_key, "daysFrom": 3},
                            timeout=30)
        resp.raise_for_status()
        for g in resp.json():
            if g.get("completed") and g.get("scores"):
                results[g["id"]] = g

    rows: list[dict] = []
    skipped = 0
    for (event_id, selection), entries in snaps.items():
        game = results.get(event_id)
        if game is None:
            skipped += 1
            continue
        entries.sort(key=lambda r: r["fetched_at"])
        opening = entries[0]
        commence = opening["commence_time"]
        before = [e for e in entries if e["fetched_at"] <= commence] or entries
        closing = before[-1]

        scores = {s["name"]: float(s["score"]) for s in game["scores"]}
        if selection not in scores:
            skipped += 1
            continue
        won = max(scores, key=scores.get)
        rows.append({
            "event_id": event_id,
            "sport": opening["sport"],
            "commence_time": commence,
            "selection": selection,
            "opening_price": opening["implied_prob"],
            "closing_price": closing["implied_prob"],
            "outcome": int(selection == won),
            "data_source": "odds-api",
        })

    if not rows:
        sys.exit("no resolvable events; accumulate more snapshots and retry")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} selection rows -> {EVENTS_CSV} "
          f"({skipped} snapshot groups skipped: not yet resolved)")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    syn = sub.add_parser("synthetic", help="generate offline simulated data")
    syn.add_argument("--events", type=int, default=1500,
                     help="number of matches to generate (default 1500)")
    syn.add_argument("--bias", type=float, default=0.22,
                     help="favorite-longshot bias strength 0..1 (default 0.22). "
                          "The default is deliberately strong enough to survive "
                          "the default vig+fee so the PIPELINE OK path is "
                          "visible; lower it (e.g. 0.08) to see a FAIL verdict.")
    syn.add_argument("--vig", type=float, default=1.05,
                     help="bookmaker overround, e.g. 1.05 = 5%% (default 1.05)")
    syn.add_argument("--seed", type=int, default=0)
    syn.set_defaults(func=cmd_synthetic)

    snap = sub.add_parser("snapshot", help="append live odds to snapshots.csv")
    snap.add_argument("--sport", required=True,
                      help="The Odds API sport key, e.g. basketball_nba")
    snap.add_argument("--regions", default="us")
    snap.set_defaults(func=cmd_snapshot)

    bld = sub.add_parser("build", help="join snapshots + scores into events.csv")
    bld.set_defaults(func=cmd_build)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
