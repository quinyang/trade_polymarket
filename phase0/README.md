# Phase 0 — Edge Existence Study

**Goal:** before building the full multi-agent system, cheaply prove (or
disprove) that a fee-surviving edge exists. This phase tests one thesis:

> **Favorite-longshot bias** — in betting markets, longshots are
> systematically overpriced and favorites underpriced. The strategy under test
> is simply: *back favorites*.

If the Phase 0 gate fails, **stop or pivot** — do not start Phase 1. See the
revised blueprint (`trading_ai_blueprint.md` on the `claude/review-project-plan-Er319`
branch) for the full rationale.

## Files

| File | Purpose |
|---|---|
| `oddslib.py` | Pure helpers: odds↔probability, devig, CLV, bootstrap CI. |
| `collect.py` | Builds `data/events.csv` — synthetic or real (The Odds API). |
| `edge_study.py` | Runs the study, writes `REPORT.md` with a PASS/FAIL verdict. |
| `data/` | Collected data (git-ignored). |
| `REPORT.md` | Generated verdict (git-ignored). |

## Setup

```bash
pip install -r ../requirements.txt
```

## Quick start (offline — pipeline self-test)

This generates simulated data with a known bias baked in, so you can verify
the analysis end-to-end without any API key:

```bash
cd phase0
python collect.py synthetic --events 1500
python edge_study.py
```

The verdict on synthetic data is capped at **`PIPELINE OK`** — it confirms the
code works but proves nothing about a real edge.

## Real data (The Odds API)

The free tier only serves *current* odds, so you accumulate snapshots over time
to capture an opening price and a closing price for each event.

```bash
export ODDS_API_KEY=your_key_here

# Run repeatedly over several days (e.g. via cron) for the sports you target.
# The first snapshot of an event becomes its opening price, the last before
# kickoff becomes its closing price.
python collect.py snapshot --sport basketball_nba

# Once enough events have finished, join snapshots + final scores:
python collect.py build

# Then run the study on the real dataset:
python edge_study.py --fee 0.02 --fav-threshold 0.60 --entry opening
```

## The Phase 0 gate

`edge_study.py` returns **`PROCEED`** only when ALL of these hold:

- the strategy placed **≥ 100 trades**
- **mean net return after fees is positive** (the EV check)
- the **bootstrap 95% CI lower bound of per-trade CLV is > 0** (the edge check)
- the data is **real** (synthetic data can never return `PROCEED`)

The confidence interval is taken on **CLV**, not net return: CLV has far lower
variance than binary-outcome P&L, so an edge is detectable in ~100 trades
instead of thousands. Net return only has to be positive on average.

Anything else is **`FAIL`** — pivot the edge source/venue and repeat Phase 0.

## Key options for `edge_study.py`

| Flag | Default | Meaning |
|---|---|---|
| `--fee` | `0.02` | Round-trip fee as a fraction of stake. Net edge must survive this. |
| `--fav-threshold` | `0.60` | Minimum market price to treat a selection as a favorite. |
| `--entry` | `opening` | Whether you transact at the opening or closing price. |
| `--bootstrap` | `10000` | Bootstrap resamples for the confidence interval. |
