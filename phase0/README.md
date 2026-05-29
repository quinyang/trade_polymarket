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
| `becker_study.py` | **PRIMARY gate.** Optimism-tax maker study on Becker's real Kalshi data. Writes `REPORT_BECKER.md`. |
| `oddslib.py` | Pure helpers: odds↔probability, devig, CLV, bootstrap CI. |
| `collect.py` | *Secondary cross-check.* Builds `data/events.csv` — synthetic or real (The Odds API). |
| `edge_study.py` | *Secondary cross-check.* Sports favorite-longshot study; writes `REPORT.md`. |
| `data/` | All datasets (git-ignored — including the 36 GB Becker download). |
| `REPORT_BECKER.md`, `REPORT.md` | Generated verdicts (git-ignored). |

## Setup

```bash
# recommended: conda env on Python 3.11
conda create -n polymarket python=3.11 && conda activate polymarket
pip install -r ../requirements.txt
```

---

## PRIMARY: the optimism-tax maker study (`becker_study.py`)

This is the gate that decides whether Phase 1 begins. It runs on Jonathan
Becker's real trade-level data — no API key, no synthetic assumptions.

### 1. Get the dataset

The dataset (~36 GB compressed Parquet, hosted on Cloudflare R2) ships with
Becker's repository. It is **not** in this git repo — it is far too large and is
git-ignored.

```bash
# Clone Becker's repo and follow its README for the R2 download link/command.
git clone https://github.com/jon-becker/prediction-market-analysis
# The repo's docs/SCHEMAS.md documents every column.

# Place (or symlink) the Kalshi data so this script can find it:
#   phase0/data/becker/kalshi/trades   (parquet file or directory)
#   phase0/data/becker/kalshi/markets  (parquet file or directory)
mkdir -p phase0/data/becker
ln -s /path/to/downloaded/kalshi phase0/data/becker/kalshi
```

> v1 uses the **Kalshi** tables only — their schema (`yes_price`, `taker_side`,
> `count`, `result`) is exactly what the optimism-tax analysis needs and is what
> Becker's headline study used. Polymarket's on-chain trade table needs separate
> preprocessing and is planned for v2.

### 2. Run it

```bash
cd phase0

# Full run (uses phase0/data/becker/kalshi by default):
python becker_study.py

# Quick run on a subset while you validate your setup:
python becker_study.py --limit 2000000

# Point at explicit paths instead of the default layout:
python becker_study.py --trades /data/kalshi/trades --markets /data/kalshi/markets
```

It writes `REPORT_BECKER.md` with all four findings (longshot bias, optimism
tax, category edge, maker/taker transfer by regime) plus the gate result.

### 3. The gate

`becker_study.py` returns **`PROCEED`** only when ALL hold:

- **≥ 100 maker fills in the recent regime** (default: on/after `2024-10-01`)
- **mean maker return after fees + adverse-selection haircut is positive**
- **bootstrap 95% CI lower bound of recent-regime maker return > 0**

The recent-regime slice is mandatory: Becker shows the edge reversed around the
Oct-2024 volume surge, so pooling all history hides the truth. The maker return
is an **upper bound** (it assumes you were the counterparty to observed flow);
the real fill rate and adverse selection are confirmed later in Phase 3 paper
trading.

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--data` | `data/becker` | Dataset root (expects `<root>/kalshi/{trades,markets}`). |
| `--taker-fee` | `0.01` | Taker fee (fraction of capital). |
| `--maker-fee` | `0.0` | Maker fee — zero on Polymarket; Kalshi varies. |
| `--maker-rebate` | `0.0` | Maker rebate if the venue pays one. |
| `--adverse-haircut` | `0.005` | Subtracted from every maker return to stress adverse selection. |
| `--recent-cutoff` | `2024-10-01` | Recent regime is on/after this date. |
| `--longshot-threshold` | `0.30` | Price ceiling for the optimism-tax slice. |
| `--limit` | (none) | Cap trade rows for a fast run. |

---

## SECONDARY cross-check: sports favorite-longshot (`collect.py` + `edge_study.py`)

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

## The secondary-check gate

`edge_study.py` returns **`PROCEED`** only when ALL of these hold (this is the
cross-check verdict, not the primary Phase 0 gate — that is `becker_study.py`):

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
