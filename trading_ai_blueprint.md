# PREDICTION MARKET TRADING AI
## Multi-Agent System — Complete Implementation Blueprint (Revised)

*For use with Claude Code or any AI coding assistant.*

> **Revision note.** This version keeps the original architecture, math, and
> risk framework but fixes the biggest weakness of the first draft: it assumed
> a tradable edge exists. It does not, by default — liquid prediction markets
> are close to efficient. This revision (a) re-sequences the plan so edge is
> proven cheaply *before* the full system is built, (b) replaces noisy P&L with
> **Closing Line Value (CLV)** as the primary success metric, (c) names the
> *specific, defensible* sources an automated edge can actually come from, and
> (d) is venue-agnostic — Polymarket is one example, not a requirement.

---

## HOW TO USE THIS DOCUMENT

This document contains the system architecture, mathematical foundations, data
sources, backtesting methodology, risk management rules, and a phased
implementation plan. Hand this entire document to a Claude Code session to
begin building — but build **Phase 0 first** and do not proceed past its gate
until edge is demonstrated.

---

## 0. The Central Problem (read this first)

Profit comes from finding events where your model assigns a materially
different probability than the market. The first draft treated that edge as
given. It is not.

- **Liquid markets are efficient.** Closing prices on major sports, elections,
  and macro releases already aggregate sharp money. Beating them persistently
  is hard; most professionals cannot.
- **Fees are larger than typical edge.** A 2% round-trip fee against a 3–5%
  gross edge leaves little or nothing. The net edge is what matters, and it is
  often zero or negative.
- **P&L is a slow, noisy detector of edge.** A 1.5 Sharpe over 200 holdout
  trades has wide confidence intervals; you cannot easily tell skill from luck.

This revision counters each of these directly. The rest of the document should
be read with one rule in mind: **the goal of Phases 0–4 is to answer "does a
fee-surviving edge exist?" — and to stop, cheaply, if the answer is no.**

### 0.1 Edge must come from a *named, structural* source

A bot does not get edge from "a better model" in the abstract. It gets edge
from a specific structural advantage. Pick one or two of these as your thesis
and design the system around it. If you cannot name your edge source, you do
not have one.

| Edge source | Why it can persist | What the bot must do well |
|---|---|---|
| **Speed / news latency** | Markets reprice on injuries, lineups, weather, headlines with a lag. A bot can react in seconds. | Low-latency news ingestion + fast order placement. Most defensible automated edge. |
| **Cross-venue line shopping** | The same event is priced differently across venues. | Connect to multiple venues; always take the best price; detect true arbitrage. |
| **Thin / newly-opened markets** | The crowd has not yet aggregated; prices are stale. Edge decays as liquidity arrives. | Trade early, trade small, exit before the crowd catches up. Tension with the liquidity floor — see §6.1. |
| **Market making / liquidity provision** | You *earn* the spread instead of paying it; fees flip from cost to income. | Two-sided quoting, inventory risk management, adverse-selection control. |
| **Structural behavioral biases** | Favorite-longshot bias, public-team overpricing, and recency bias are documented and persistent in sports. | Systematically fade the bias; measure that it still exists in-sample and out-of-sample. |

### 0.2 Venue selection is part of the edge

You are not bound to any one venue. Choose venues the way you choose a model.

- **Fee structure dominates.** Prefer venues with maker rebates or no maker
  fee, or commission charged only on net winnings. Being a *maker* (posting
  limit orders) instead of a *taker* can turn the largest cost into income.
- **Liquidity vs. edge trade-off.** Deep venues are efficient (low edge, low
  slippage); thin venues have edge but high slippage and manipulation risk.
  Decide deliberately which side of this you are exploiting.
- **Resolution risk.** On-chain markets (e.g. Polymarket via the UMA oracle)
  can have disputed or delayed settlement; exchanges like Kalshi resolve
  centrally; betting exchanges resolve fast. Model this as a real cost.
- **Operational reality.** On-chain venues require a funded wallet, gas, and
  have geographic restrictions; treat them as more than "a REST API."

---

## 1. System Overview

Prediction markets price contracts that pay $1 if an event occurs and $0 if it
does not. The contract price equals the crowd's implied probability. This
system is a multi-agent AI pipeline that automates the full cycle: data
collection, probability estimation, edge detection, peer validation between
agents, risk-adjusted position sizing, order execution, and continuous learning
from outcomes.

### 1.1 Best Markets to Target

Ranked by mathematical tractability — how cleanly data maps to outcomes — and
annotated with where a structural edge (§0.1) is most plausible:

| Market type | Why it works | Plausible edge source | Key data source |
|---|---|---|---|
| NBA / NFL / Soccer match results | Binary outcome, decades of stats, Elo models work well | News latency; behavioral bias | API-Football, stats.nba.com |
| Player performance props | Normal/Poisson distribution, per-game stats | News latency (lineup/minutes); thin markets | Sportradar, RapidAPI Sports |
| Economic data releases | Consensus forecasts available, resolves same day | Speed on the release print | FRED, Bloomberg consensus |
| US election markets | Polling aggregation models translate directly | Cross-venue; thin down-ballot markets | FiveThirtyEight data, polls APIs |
| Weather events | Meteorological ensemble models | Speed on model updates | NOAA, OpenWeatherMap |
| Geopolitical events | AVOID early — subjective, thin data, slow resolution | None reliable | N/A |

---

## 2. Multi-Agent Architecture

A single agent cannot safely trade. The system requires specialized agents that
check each other's work before any money moves. This mirrors how professional
quant funds operate — separate teams for research, risk, and execution with
hard gates between them.

> **Build order matters.** Do not build agents 3–10 until Phase 0 proves edge.
> See §7.

### 2.1 The Agents

| Agent | Layer | Responsibility | Outputs |
|---|---|---|---|
| Data Harvester | Data | Pulls odds, live scores, historical stats, news feeds | Clean normalized feature rows |
| Sentiment Agent | Data | Runs NLP on news + social media | Sentiment score −1.0 to +1.0 per event |
| Stats Model Agent | Intelligence | ML probability estimate from structured features | P_stats: float 0–1 |
| Sentiment Model Agent | Intelligence | Adjusts probability using sentiment signal | P_sentiment: float 0–1 |
| Orchestrator Agent | Coordination | Merges model outputs, routes to validation, decides trade or skip | P_final, trade signal |
| Devil's Advocate Agent | Validation | Actively challenges each prediction, searches for counter-evidence | VETO or CONFIRM signal |
| Calibration Agent | Validation | Audits model accuracy + **CLV** over rolling window | Drift alert or OK |
| Risk + Kelly Agent | Execution | Computes Kelly stake, enforces hard limits | Stake size in dollars |
| Execution Agent | Execution | Places orders via venue REST API; prefers maker orders | Order confirmation / error |
| Monitor + Retrain Agent | Learning | Tracks P&L **and CLV**, triggers retraining when drift detected | Retrain signal, Sharpe + CLV report |

### 2.2 How Agent Validation Works

The **Devil's Advocate** agent challenges every prediction: given the
prediction plus the raw data, "What is wrong with this prediction? Find the
strongest counter-argument." If it finds a recent lineup change the model
missed, a history of model failure on similar matchups, unusual liquidity
suggesting smarter money is on the other side, or conflicting data the model
underweighted, it returns a **VETO**. A VETO requires a 2× higher edge
threshold to override.

> **The Devil's Advocate must itself be validated (new).** An LLM asked "what's
> wrong with this?" will *always* find something — left unchecked it may just
> suppress good trades. Treat it as a model with its own backtest. During
> Phase 4, log every VETO/CONFIRM and measure: do VETO'd trades actually have
> worse realized CLV and P&L than CONFIRM'd trades? If they do not, the agent
> is noise — disable it or retune it. Track VETO precision and recall like any
> classifier.

The **Calibration Agent** computes the Brier Score over the last 100 resolved
predictions **and the CLV distribution over the last 100 trades** (see §3G).

**FORMULA — Brier Score**
```
Brier Score = (1/n) × Σ(P_predicted − outcome)²
```
A perfect model scores 0.0. A random model scores 0.25. Target: Brier < 0.18.
Above 0.22 triggers automatic retraining and halts new trades.

---

## 3. Mathematical Methods

### 3A. Probability Estimation

**Bayesian Updating.** Start with a prior probability (historical base rate)
and update it as evidence arrives.

**FORMULA — Bayes' Theorem**
```
P(outcome | evidence) = P(evidence | outcome) × P(outcome) / P(evidence)
```

**Logistic Regression / ML Model.** The core probability model maps a feature
vector X to a calibrated probability:

**FORMULA — Logistic / ML Model**
```
P(outcome) = 1 / (1 + e^−(β₀ + β₁x₁ + β₂x₂ + ... + βₙxₙ))
```
Train with GradientBoostingClassifier or XGBoost. Calibrate with
`CalibratedClassifierCV(method='isotonic')`. Calibration is mandatory — raw
model scores are not true probabilities.

### 3B. Edge Calculation

**FORMULA — Edge**
```
Edge = P_model − P_market
```
Only trade when **net** edge (after fees and expected slippage) exceeds the
threshold. See §3D — gross edge is not tradable.

### 3C. Kelly Criterion — Optimal Position Sizing

**FORMULA — Kelly Criterion**
```
f* = (b × p − q) / b
  b = net odds = (1 / P_market) − 1
  p = P_model
  q = 1 − p
  f* = fraction of bankroll to bet
```
Always use **Fractional Kelly**: `f_actual = 0.25 × f*`. Never use full Kelly.

### 3D. Expected Value Filter — fee-aware

**FORMULA — Net Expected Value**
```
EV_net = (P_model × profit_if_win) − ((1 − P_model) × stake_if_lose) − fees − expected_slippage
```
Only place trades where `EV_net > 0`. **Fees and slippage are subtracted before
the decision, not after.** A positive gross edge that is negative net must be
blocked. Maker orders (which may earn a rebate) and taker orders have different
fee terms — the Risk Agent must know which it is placing.

### 3E. Arbitrage Detection — including cross-venue

**FORMULA — Arbitrage Detection**
```
Arbitrage exists when: Σ(1 / odds_i) < 1.0 across all outcomes
```
Check this **within a venue and across venues** — buying YES cheaply on venue A
and NO cheaply on venue B is the same calculation and is often where the only
truly risk-free profit lives. Cover all outcomes proportionally. Subtract fees
on every leg before declaring an arb.

### 3F. Performance Metrics

| Metric | Formula | Pass threshold | Meaning |
|---|---|---|---|
| **CLV (primary)** | Mean(entry_price − closing_price), in your favor | **> 0, statistically significant** | Did you beat the market's final price? |
| Sharpe Ratio | (Mean Return − Risk-free Rate) / Std Dev | > 1.5 | Risk-adjusted profitability |
| Max Drawdown | Max peak-to-trough loss in period | < 20% | Worst losing streak survivable |
| Brier Score | (1/n) × Σ(P − outcome)² | < 0.18 | Probability accuracy |
| ROI (net) | (Total P&L / Capital Deployed) − 1 | > 0% | Net profitability after fees |
| Win Rate on +EV | Wins / Total +EV trades | > 52% | Edge detection accuracy |
| Calmar Ratio | Annual Return / Max Drawdown | > 1.0 | Return per unit of drawdown risk |

### 3G. Closing Line Value — the primary edge detector (new)

**The problem CLV solves.** P&L is a slow, noisy proxy for edge: variance
dominates over hundreds of trades. CLV is fast and high-signal.

**FORMULA — Closing Line Value**
```
CLV (per trade) = (closing_market_prob − entry_market_prob), signed so that
                  positive = you got a better price than the market's close
Beat-close rate = fraction of trades with CLV > 0
```

**Why it works.** The closing price is the market's best, most-informed
estimate. If you *consistently* transact at prices better than the close, you
have edge — this is provable in dozens of trades, not thousands, and it does
not require waiting for events to resolve. P&L follows CLV with a lag.

**How to use it.**
- It is the **Phase 0 gate metric** (§7) and the primary metric everywhere.
- Test significance: a one-sample t-test (or bootstrap CI) on the per-trade CLV
  series. Require the lower bound of the 95% CI to be > 0.
- A system with positive CLV but negative P&L has an *execution/fee* problem,
  not an edge problem — diagnosable. The reverse (positive P&L, zero CLV) is
  just luck and will revert.
- The Monitor Agent reports rolling CLV; a drop to ≤ 0 is a halt trigger.

---

## 4. Data Sources & Labels

### 4.1 Data Source Directory

| Source | URL | What it provides | Cost |
|---|---|---|---|
| The Odds API | the-odds-api.com | Historical bookmaker odds (P_market) | Free tier + paid |
| API-Football | api-football.com | Match results — WIN/LOSS labels | Free tier + paid |
| stats.nba.com | stats.nba.com/stats/ | NBA game + player stats | Free (no key) |
| Sportradar | sportradar.com | Pro-grade stats, live feeds | Paid |
| FRED | fred.stlouisfed.org/docs/api/ | Economic releases + outcomes | Free |
| GDELT / NewsAPI | newsapi.org | News headlines for sentiment | Free tier |
| Polymarket CLOB API | docs.polymarket.com | Market prices + resolutions | Free |
| Polymarket Subgraph | thegraph.com/explorer/ | Full history via GraphQL | Free |
| Kalshi API | kalshi.com | Centrally-resolved event contracts | Free |
| Betfair Exchange API | developer.betfair.com | Low-vig exchange odds, maker liquidity | Free (commission on winnings) |
| OpenWeatherMap | openweathermap.org/api | Weather features for outdoor events | Free tier |

> **Capture closing prices.** Whatever venue you use, you must record the
> market price at (or just before) event start for every event — CLV is
> impossible without it. Add a `closing_price` column and a job that snapshots
> it.

### 4.2 Label Construction

Every training row needs: features (timestamped before event start), P_market
(price at bet time), `closing_price` (price at event start), and label
(YES=1 / NO=0). Labels come from API-Football / stats.nba.com final scores,
FRED actual releases vs. the question threshold, or the venue's resolution
field.

> **CRITICAL — Lookahead Bias Prevention.** Every feature must be timestamped
> BEFORE the event start time. Enforce a strict `as_of` timestamp on every data
> pull. Using post-event data is cheating — the model looks brilliant
> historically and fails live.

---

## 5. Backtesting & Validation Pipeline

### 5.1 Testing Phases

No real money until all phases pass. Each phase catches failure modes the
previous one cannot.

**Phase 0 — Edge Existence Study (new, comes first).** Before building agents,
cheaply test whether your chosen edge source (§0.1) produces positive CLV. See
§7 Phase 0.

**Phase 1 — Historical Backtesting.** Replay 2+ years of historical events.
For each: pull features as of bet time, run the model, compute net edge,
simulate Kelly stake, record P&L *and CLV* using the real outcome and closing
price.
- Dataset: minimum 500 events, ideally 1,500+.
- Split: 70% train, 15% validation, 15% holdout test (never touch test early).
- Pass: CLV CI lower bound > 0, Sharpe > 1.5, Drawdown < 20%, ROI > 0%,
  Brier < 0.18.

**Phase 2 — Walk-Forward Testing.** Train on months 1–12, test on month 13;
roll forward by one month. Proves generalization.
- Pass: consistent Sharpe > 1.2 *and* positive CLV across all out-of-sample
  windows.

**Phase 3 — Paper Trading (Simulated Live).** Live data, real decisions,
simulated money. Tests execution, latency, data-format drift, edge cases.
- Duration: minimum 30 days, ideally 90.
- Pass: positive CLV, positive simulated P&L, no critical execution failures.

### 5.2 The Four Deadly Sins of Backtesting

1. **Lookahead bias** — using data that did not exist at bet time. Fix: strict
   `as_of` timestamps.
2. **Overfitting** — model memorizes history. Symptom: great backtest, random
   walk-forward. Fix: limit complexity, walk-forward testing, L1/L2
   regularization.
3. **Survivorship bias** — testing only on cleanly-completed events. Fix:
   include cancelled/postponed/voided events; handle "no action" explicitly.
4. **Transaction cost blindness** — ignoring fees and slippage. Fix: subtract
   realistic fees + bid-ask spread + liquidity-dependent slippage from every
   simulated trade.

> **Fifth sin (new) — backtesting against stale prices.** If your edge is
> *speed* (§0.1), a backtest using daily or hourly price snapshots cannot
> measure it — you need tick-level or event-stamped price history, or the
> backtest will silently overstate edge. If you cannot get that granularity,
> the speed edge can only be validated in Phase 3 paper trading.

---

## 6. Risk Management Rules

Risk management is not optional and cannot be overridden by any agent. These
are hard circuit breakers encoded into the Risk + Kelly Agent.

### 6.1 Position-Level Rules

| Rule | Limit | Reason |
|---|---|---|
| Max single position | 5% of bankroll | One bad trade cannot be catastrophic |
| Fractional Kelly multiplier | 0.25× | Model probabilities are imperfect |
| Minimum **net** edge to trade | 4–6% after fees + slippage | Below this, variance swamps edge |
| Minimum market liquidity | Venue-dependent; set per edge thesis | Thin markets = slippage + manipulation **but also where thin-market edge lives — see note** |
| Max position duration | Defined at open | No holding through unclear resolution |

> **Liquidity floor vs. thin-market edge.** The original $10k floor conflicts
> with the thin-market edge source (§0.1). Resolve this explicitly: if your
> thesis is thin-market edge, lower the floor but also lower max position size
> and model slippage aggressively. If your thesis is speed or behavioral bias
> on liquid markets, keep the high floor. One config, chosen deliberately.

### 6.2 Portfolio-Level Rules

| Rule | Limit | Reason |
|---|---|---|
| Max total exposure | 40% of bankroll deployed | Capital reserve for drawdown recovery |
| Max correlated exposure | 15% in same sport/category | Correlated events = correlated losses |
| Drawdown halt threshold | 20% peak-to-trough | Halt, audit model before resuming |
| Daily loss limit | 5% of bankroll per day | Prevents single-day blowup |
| Max trades per day | Configurable, default 10 | Prevents overtrading on false signals |
| Cross-venue counterparty cap | Max % of bankroll on any one venue | On-chain/exchange failure is a real risk |

### 6.3 Model Health Rules

| Trigger | Action | Recovery |
|---|---|---|
| Brier > 0.22 over last 100 trades | Halt new trades, flag for retraining | Resume after retrain + walk-forward retest |
| **Rolling CLV ≤ 0 over last 50 trades** | **Halt new trades — edge has decayed** | **Resume only after re-running Phase 0 edge study** |
| 3 consecutive losing days | Reduce all position sizes 50% | Restore after 5 profitable days |
| API error rate > 5% | Halt execution, alert operator | Resume after root cause fixed + tested |
| Walk-forward Sharpe < 0.8 | Full system halt | Manual review + model rebuild |
| Devil's Advocate VETO rate > 60% | Investigate model vs. market alignment | Usually signals regime change |

> **THE FUNDAMENTAL RISK PRINCIPLE.** Your edge is real but small — typically
> 3–8% gross, often far less net. Risk management's job is to ensure you
> survive long enough for that edge to compound. A 50% drawdown needs a 100%
> gain to recover. One catastrophic trade undoes 200 good ones. Survival is the
> prerequisite for profit.

---

## 7. Implementation Plan (re-sequenced)

The original plan built the full 10-agent system *before* proving edge — 8+
weeks of work that is wasted if no edge exists. This revision puts a cheap
fail-fast study first.

### Phase 0 — Edge Existence Study (Weeks 1–3) — *fail fast here*

**Goal: prove a fee-surviving edge exists before building anything else.**

- Pick **one** edge source (§0.1) and **one** venue as your thesis.
- Build a minimal data pull: events, `entry_price`, `closing_price`, outcome
  label. No agents, no database fanciness — a script and a CSV/SQLite is fine.
- Build a single baseline model (logistic regression or XGBoost) OR, for a
  speed/arb thesis, just a rule.
- On a holdout set, measure **CLV** and net EV after realistic fees.
- **GATE:** per-trade CLV is positive with a 95% CI lower bound > 0 on ≥ 100
  out-of-sample events, *and* net EV > 0 after fees.
- **If the gate fails:** stop, or pivot to a different edge source/venue and
  repeat Phase 0. **Do not proceed to Phase 1.** This is the most important
  gate in the document — it is designed to kill the project cheaply.

### Phase 1 — Data Pipeline (Weeks 4–5)

**Goal: reliable, labeled historical dataset ready for model training.**

- Set up PostgreSQL: `events`, `features`, `market_prices`, `closing_prices`,
  `outcomes`, `trades`, `model_runs`.
- Build the Data Harvester Agent with strict `as_of` enforcement.
- Backfill 2+ years; snapshot closing prices for every event.
- Build the label pipeline; manually validate 50 random rows.
- Build the Sentiment Agent (NewsAPI + VADER/FinBERT).
- **Gate:** 1,000+ complete rows with features + P_market + closing_price +
  label. Zero lookahead violations.

### Phase 2 — Probability Models (Weeks 6–7)

**Goal: calibrated models that beat the closing line on held-out data.**

- Feature engineering: home advantage, rolling win rate, head-to-head, rest
  days, sentiment, line drift.
- Train Stats Model: XGBoost + `CalibratedClassifierCV(method='isotonic')`.
- Train Sentiment Model: logistic regression on sentiment + odds features.
- Reliability diagram; CLV by prediction bucket.
- **Gate:** Brier < 0.20 on holdout; walk-forward Sharpe > 1.0; **positive CLV
  on holdout**.

### Phase 3 — Agent System (Weeks 8–10)

**Goal: full multi-agent pipeline running end-to-end in simulation.**

- Orchestrator (merge P_stats/P_sentiment, 70/30 to start).
- Devil's Advocate (LLM, structured VETO/CONFIRM) — **and log every decision
  for later validation (§2.2)**.
- Calibration Agent (rolling Brier + rolling CLV).
- Risk + Kelly Agent (Kelly + all §6 hard limits).
- Execution Agent (venue REST API; sandbox/testnet first; prefer maker orders).
- Monitor + Retrain Agent (P&L, CLV, Sharpe, drawdown, retrain trigger).
- Integration test on 100 historical events.
- **Gate:** all agents communicate correctly, no orphaned trades, kill switches
  work.

### Phase 4 — Backtesting & Validation (Weeks 11–14)

**Goal: system passes all testing phases — including Devil's Advocate
validation — before any real money.**

- Phase 1 backtest: replay 2-year dataset through the full pipeline.
- Compute CLV, Sharpe, Drawdown, ROI, Brier, Win Rate vs. §5 thresholds.
- Phase 2 walk-forward: rolling window; check Sharpe and CLV consistency.
- **Validate the Devil's Advocate:** compare realized CLV/P&L of VETO'd vs.
  CONFIRM'd trades; disable or retune if VETOs do not improve outcomes.
- Manually review 20 random trades for the deadly sins.
- Phase 3 paper trading: live data, simulated trades, 30+ days.
- **Gate:** all metrics pass; paper-trading CLV and P&L positive; anomalies
  documented.

### Phase 5 — Live Deployment (Weeks 15–18)

**Goal: live system with real capital at minimum Kelly fraction.**

- Start with 10% of intended capital — treat as extended paper trading.
- All §6 position limits at 50% of stated values for the first 30 days.
- Monitor daily: CLV, P&L, Brier, Sharpe, drawdown, API error rate.
- Run Monitor + Retrain weekly.
- Scale capital only as Sharpe *and* CLV sustain above target over rolling
  30-day windows.
- **Gate:** 90-day live Sharpe > 1.5, positive CLV, max drawdown never
  exceeded 10%, system stable.

> Estimated total time for a solo developer: **10–18 weeks** — longer than the
> original 8–14, mostly because Phase 0 and honest paper-trading windows are
> not compressible.

---

## 8. Recommended Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Models, pipeline, agent logic |
| ML Models | XGBoost, scikit-learn | Stats model, calibration |
| NLP / Sentiment | HuggingFace Transformers (FinBERT) | News + social sentiment |
| LLM Agents | Anthropic Claude API (claude-sonnet-4-6) | Devil's Advocate, Orchestrator |
| Database | PostgreSQL + Redis | Historical data + real-time cache |
| Data Pipeline | Apache Airflow or cron + Python | Scheduled pulls, feature updates |
| Backtesting | Custom vectorized Python framework | Replay engine with fee + CLV simulation |
| API Client | httpx + asyncio | Non-blocking venue API calls |
| Monitoring | Grafana + InfluxDB or Postgres | Live P&L, CLV, drift dashboards |
| Deployment | Docker + Linux VPS (low latency) | Always-on execution near the venue |

### 8.1 Key Python Libraries

`xgboost`, `scikit-learn`, `pandas`, `httpx`, `anthropic`, `sqlalchemy`,
`schedule` (or `airflow`), `pytest`, `scipy` (CLV significance testing).

---

## 9. Instructions for Claude Code Session

> Paste this section at the start of your Claude Code session along with this
> document.

You are building a multi-agent prediction market trading system. This document
is the complete specification. **Build Phase 0 first and do not pass its gate
until positive CLV is demonstrated.** Then follow phases in order; do not skip
gates.

**Phase 0 files (build these first):**
- `phase0/collect.py` — minimal event + entry-price + closing-price + outcome
  collector for one venue.
- `phase0/edge_study.py` — baseline model/rule, holdout CLV computation, t-test
  / bootstrap CI on per-trade CLV, net-EV-after-fees check.
- `phase0/REPORT.md` — written verdict: does the edge exist? proceed or pivot?

**Full-system files (only after the Phase 0 gate passes):**
- `db/schema.sql` — `events`, `features`, `market_prices`, `closing_prices`,
  `outcomes`, `trades`, `model_runs`.
- `agents/data_harvester.py`, `agents/sentiment_agent.py`,
  `models/stats_model.py`, `models/sentiment_model.py`,
  `agents/orchestrator.py`, `agents/devils_advocate.py`,
  `agents/calibration_agent.py`, `agents/risk_kelly.py`,
  `agents/execution_agent.py`, `agents/monitor_agent.py`,
  `backtest/engine.py`, `backtest/walk_forward.py`, `tests/`.

**Critical implementation constraints:**
- Every data pull must accept an `as_of` datetime; reject feature rows missing
  it.
- Every event must record a `closing_price`; CLV is a first-class metric, not
  an afterthought.
- The Risk + Kelly Agent hard limits cannot be overridden by any agent or
  config flag.
- All EV/edge checks are **net of fees and slippage**.
- The Devil's Advocate uses the Claude API (`claude-sonnet-4-6`), returns
  structured JSON VETO/CONFIRM, and every decision is logged for Phase 4
  validation.
- Paper vs. live is one environment variable: `TRADING_MODE=paper|live`.
- All trades (real and simulated) write to `trades` with a full audit trail.
- Retraining is auto-triggered by the Monitor Agent but requires human
  confirmation before a new model goes to production.

> **FINAL WARNING.** Never deploy Phase 5 (live trading) until all Phase 4 gates
> pass. And never proceed past **Phase 0** until CLV proves an edge exists —
> that gate is what separates a profitable system from an expensive lesson.

*End of Revised Implementation Blueprint.*
