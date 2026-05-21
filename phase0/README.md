# Phase 0 — Edge Existence Study

**Goal:** before building the full multi-agent system, cheaply prove (or
disprove) that a fee-surviving edge exists. If the Phase 0 gate fails, **stop or
pivot** — do not start Phase 1. See `trading_ai_blueprint.md` (§0.3, §7) for the
full rationale.

## Primary thesis (updated): the "optimism tax" as a maker

Following Becker's *Microstructure of Wealth Transfer in Prediction Markets*
(72.1M trades, $18.26B volume), the **primary** Phase 0 study is the optimism
tax:

> Takers systematically overpay for cheap **"YES" longshots**; those YES
> longshots underperform the equivalent **"NO" longshots by up to 64 pp**. The
> edge is captured by the **maker** who sells into that flow — **no forecast
> required**, and on Polymarket makers pay **zero fees** (takers pay 0.75–1.8%),
> so the strategy is fee-positive.

The real test runs on **Becker's dataset**
(github.com/jon-becker/prediction-market-analysis), which contains trade-level
maker/taker flow. The study must (a) confirm the YES-vs-NO longshot gap, (b)
simulate the maker side with a **fill model** and **adverse-selection haircut**,
and (c) **slice by time** — Becker shows the edge reversed around the Oct-2024
volume surge, so it must be present in the *recent* regime.

> **This Becker maker study is not yet coded** — it is the next deliverable. The
> code currently in this folder is the secondary cross-check below.

## Secondary cross-check (the code in this folder): favorite-longshot on sports

> **Favorite-longshot bias** — in betting markets, longshots are systematically
> overpriced and favorites underpriced. The strategy under test is *back
> favorites* (the taker-side, sports-odds version of the same bias). It is a
> useful confirmation that the bias generalises beyond Polymarket, but it is
> **not** the gate that matters.

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
