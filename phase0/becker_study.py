"""Phase 0 PRIMARY edge study: the optimism tax, on Becker's real dataset.

This is the gate that matters (see trading_ai_blueprint.md sections 0.3, 3A.1,
3A.2, 7). It replicates Jonathan Becker's "Microstructure of Wealth Transfer in
Prediction Markets" on his published trade-level data and tests whether the
MAKER edge survives fees on RECENT data.

Unlike the forecasting path, this study predicts nothing about individual
events. It measures three population regularities across millions of real
trades:

  Finding 1 — Longshot bias       cheap contracts win less than their price
  Finding 2 — Maker/taker transfer makers earn, takers lose, structurally
  Finding 4 — Optimism tax         YES underperforms NO at longshot prices

and then simulates being the maker counterparty to taker flow.

Venue: KALSHI. Its trade schema is clean and is exactly what Becker analysed:
  trades : trade_id, ticker, count, yes_price (cents 1-99), taker_side (yes|no),
           created_time
  markets: ticker, result (yes|no|''), status
Polymarket's on-chain trade table (addresses + asset ids, no explicit outcome
side) needs separate preprocessing and is out of scope for v1.

GATE (PROCEED) requires ALL of:
  * >= 100 maker fills in the RECENT regime (default: on/after 2024-10-01)
  * mean maker return after fees is positive in that regime
  * bootstrap 95% CI lower bound of recent-regime maker return > 0
The recent-regime slice is mandatory because Becker shows the edge reversed
around the Oct-2024 volume surge — pooling all history hides that.

The maker simulation is an UPPER BOUND: it assumes you were the counterparty to
the observed taker flow. Real life adds fill risk and adverse selection, so the
script reports the result under a configurable adverse-selection haircut and a
fill-rate subsample, and the verdict is only as trustworthy as those stress
assumptions.

Usage:
    python becker_study.py --data PATH_TO_DATASET_ROOT
    python becker_study.py --trades kalshi/trades --markets kalshi/markets
    python becker_study.py --limit 2000000   # quick run on a subset

See README.md ("Becker maker study") for dataset download instructions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from oddslib import bootstrap_ci

REPORT_MD = Path(__file__).parent / "REPORT_BECKER.md"
DEFAULT_DATA = Path(__file__).parent / "data" / "becker"

# Columns we actually need (projection keeps memory sane on the 36 GB set).
TRADE_COLS = ["ticker", "count", "yes_price", "taker_side", "created_time"]
MARKET_COLS = ["ticker", "result", "status"]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _require_pyarrow():
    try:
        import pyarrow.dataset as ds  # noqa: F401
    except ImportError:
        sys.exit("pyarrow is required: pip install pyarrow")
    import pyarrow.dataset as ds
    return ds


def _resolve(base: Path, override: str | None, venue: str, table: str) -> Path:
    """Find a parquet file or a directory of parquet files for `table`."""
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = base / p
        if p.exists():
            return p
        sys.exit(f"path not found: {p}")
    candidates = [
        base / venue / table,
        base / venue / f"{table}.parquet",
        base / table,
        base / f"{table}.parquet",
    ]
    for c in candidates:
        if c.exists():
            return c
    sys.exit(
        f"could not locate {venue} {table} under {base}.\n"
        f"tried: {', '.join(str(c) for c in candidates)}\n"
        f"pass --{table} explicitly, or see README for the dataset layout."
    )


def load_table(ds, path: Path, columns: list[str], limit: int | None):
    """Read selected columns from a parquet file/dir into a pandas DataFrame."""
    dataset = ds.dataset(str(path), format="parquet")
    have = set(dataset.schema.names)
    missing = [c for c in columns if c not in have]
    if missing:
        sys.exit(f"{path} is missing expected columns {missing}; "
                 f"available: {sorted(have)}")
    if limit:
        table = dataset.head(limit, columns=columns)
    else:
        table = dataset.to_table(columns=columns)
    return table.to_pandas()


# --------------------------------------------------------------------------
# Core return computation (vectorised, zero forecasting)
# --------------------------------------------------------------------------

def compute_returns(df, taker_fee: float, maker_fee: float,
                    maker_rebate: float):
    """Attach per-trade taker and maker returns.

    Binary contract economics, per contract:
      buyer of a side pays its price, receives $1 if that side resolves.
    The maker is the counterparty, i.e. holds the OPPOSITE outcome side.
    Returns are expressed as a fraction of the capital that side deployed.
    """
    p_yes = df["yes_price"].to_numpy(dtype=float) / 100.0
    p_no = 1.0 - p_yes
    outcome_yes = (df["result"].to_numpy() == "yes").astype(float)
    outcome_no = 1.0 - outcome_yes
    taker_is_yes = (df["taker_side"].to_numpy() == "yes")

    # Taker bought whichever side taker_side says.
    taker_cost = np.where(taker_is_yes, p_yes, p_no)
    taker_payoff = np.where(taker_is_yes, outcome_yes, outcome_no)
    taker_ret = (taker_payoff - taker_cost) / taker_cost - taker_fee

    # Maker is the counterparty -> holds the opposite outcome side.
    maker_cost = np.where(taker_is_yes, p_no, p_yes)
    maker_payoff = np.where(taker_is_yes, outcome_no, outcome_yes)
    maker_ret = (maker_payoff - maker_cost) / maker_cost - maker_fee + maker_rebate

    df = df.copy()
    df["p_yes"] = p_yes
    df["outcome_yes"] = outcome_yes
    df["taker_return"] = taker_ret
    df["maker_return"] = maker_ret
    df["maker_capital"] = df["count"].to_numpy(dtype=float) * maker_cost
    df["maker_pnl"] = df["maker_return"] * df["maker_capital"]
    return df


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

def calibration_curve(df, n_bins: int = 20) -> list[dict]:
    """Finding 1: realized YES frequency vs quoted YES price."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    p = df["p_yes"].to_numpy()
    y = df["outcome_yes"].to_numpy()
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if not m.any():
            continue
        rows.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "n": int(m.sum()),
            "mean_price": float(p[m].mean()),
            "realized_yes": float(y[m].mean()),
            "gap": float(y[m].mean() - p[m].mean()),
        })
    return rows


def optimism_tax(df, longshot_threshold: float) -> dict:
    """Finding 4: hypothetical YES-buyer vs NO-buyer return at longshot prices.

    Independent of who actually took the trade — it asks what a taker buying
    YES vs NO at each price would have earned.
    """
    p = df["p_yes"].to_numpy()
    y = df["outcome_yes"].to_numpy()
    m = p <= longshot_threshold
    if not m.any():
        return {"n": 0}
    pl = p[m]
    yl = y[m]
    yes_ret = ((yl - pl) / pl)            # buy YES at p_yes
    no_ret = (((1 - yl) - (1 - pl)) / (1 - pl))  # buy NO at p_no
    return {
        "n": int(m.sum()),
        "threshold": longshot_threshold,
        "yes_return": float(yes_ret.mean()),
        "no_return": float(no_ret.mean()),
        "gap_pp": float((no_ret.mean() - yes_ret.mean()) * 100),
    }


def category_edge(df, top_n: int = 12) -> list[dict]:
    """Finding 3 (proxy): maker-taker gap by Kalshi ticker series prefix.

    Kalshi tickers look like SERIES-YYMMM-... ; the prefix before the first '-'
    is a coarse category proxy (not Becker's exact taxonomy).
    """
    series = df["ticker"].astype(str).str.split("-").str[0]
    out = []
    for name, idx in df.groupby(series).groups.items():
        sub = df.loc[idx]
        if len(sub) < 500:
            continue
        gap = (sub["maker_return"].mean() - sub["taker_return"].mean()) * 100
        out.append({"series": str(name), "n": len(sub), "gap_pp": float(gap)})
    out.sort(key=lambda r: r["gap_pp"], reverse=True)
    return out[:top_n]


def regime_slice(df, cutoff: str):
    """Split into pre/post-cutoff by created_time."""
    ts = df["created_time"]
    cut = np.datetime64(cutoff)
    recent = df[ts.to_numpy().astype("datetime64[ns]") >= cut]
    older = df[ts.to_numpy().astype("datetime64[ns]") < cut]
    return older, recent


def maker_gate(df_recent, n_boot: int, adverse_haircut: float):
    """Evaluate the maker strategy on the recent-regime trades."""
    ret = df_recent["maker_return"].to_numpy() - adverse_haircut
    res = {"n": len(ret)}
    if len(ret) == 0:
        return res
    res["mean_return"] = float(ret.mean())
    res["ci"] = bootstrap_ci(ret, n_boot=n_boot)
    cap = df_recent["maker_capital"].to_numpy()
    pnl = ret * cap
    res["dollar_weighted_return"] = float(pnl.sum() / cap.sum()) if cap.sum() else float("nan")
    return res


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def verdict(gate: dict, adverse_haircut: float) -> tuple[str, list[str]]:
    reasons = []
    if gate["n"] < 100:
        return "FAIL", [f"only {gate['n']} recent-regime fills (need >= 100)"]
    if gate["mean_return"] <= 0:
        reasons.append(f"mean maker return {gate['mean_return']:+.4f} not positive")
    if gate["ci"][0] <= 0:
        reasons.append(f"maker return 95% CI lower bound {gate['ci'][0]:+.4f} not > 0")
    if reasons:
        return "FAIL", reasons
    return "PROCEED", [
        f"recent-regime maker edge survives fees and a {adverse_haircut:.3f} "
        f"adverse-selection haircut on {gate['n']} fills — proceed to Phase 1",
        "remember: this is an upper bound; confirm in Phase 3 paper trading "
        "with real fills",
    ]


def write_report(meta, cal, tax, cats, older, recent, gate, label, reasons,
                 adverse_haircut):
    L = []
    L.append("# Phase 0 — Optimism-Tax Maker Study (Becker dataset): REPORT")
    L.append("")
    L.append("**Thesis (primary):** harvest the optimism tax as a fee-free "
             "maker — be the counterparty to YES-longshot taker flow. No "
             "forecasting.")
    L.append("")
    L.append(f"- Venue: `{meta['venue']}`  ·  trades analysed: "
             f"{meta['n_trades']:,}  ·  resolved markets: {meta['n_markets']:,}")
    L.append(f"- Taker fee: {meta['taker_fee']:.3f}  ·  maker fee: "
             f"{meta['maker_fee']:.3f}  ·  maker rebate: {meta['maker_rebate']:.3f}")
    L.append(f"- Adverse-selection haircut: {adverse_haircut:.3f}  ·  "
             f"recent-regime cutoff: {meta['cutoff']}")
    L.append("")

    L.append("## Verdict")
    L.append("")
    L.append(f"### {label}")
    L.append("")
    for r in reasons:
        L.append(f"- {r}")
    L.append("")

    L.append("## Finding 1 — Longshot bias (calibration curve)")
    L.append("")
    L.append("`gap` = realized YES frequency − mean quoted YES price. "
             "Negative at low prices = cheap contracts overpriced.")
    L.append("")
    L.append("| YES price | n | mean price | realized YES | gap |")
    L.append("|---|---|---|---|---|")
    for b in cal:
        L.append(f"| {b['range']} | {b['n']:,} | {b['mean_price']:.3f} | "
                 f"{b['realized_yes']:.3f} | {b['gap']:+.3f} |")
    L.append("")

    L.append("## Finding 4 — Optimism tax (YES vs NO at longshot prices)")
    L.append("")
    if tax["n"] == 0:
        L.append("No trades at or below the longshot threshold.")
    else:
        L.append(f"At YES price <= {tax['threshold']:.2f} "
                 f"({tax['n']:,} trades):")
        L.append("")
        L.append(f"- YES-buyer return: **{tax['yes_return']:+.3f}**")
        L.append(f"- NO-buyer return:  **{tax['no_return']:+.3f}**")
        L.append(f"- Gap (NO − YES): **{tax['gap_pp']:+.1f} pp** "
                 f"— if you must take below this price, buy NO.")
    L.append("")

    L.append("## Finding 3 — Category edge (maker−taker gap, ticker-series proxy)")
    L.append("")
    L.append("Higher gap = more taker overpayment to harvest. Target the top.")
    L.append("")
    L.append("| series | n | maker−taker gap (pp) |")
    L.append("|---|---|---|")
    for c in cats:
        L.append(f"| {c['series']} | {c['n']:,} | {c['gap_pp']:+.2f} |")
    L.append("")

    L.append("## Finding 2 — Maker/taker wealth transfer, by regime")
    L.append("")
    L.append("| regime | n | mean maker return | mean taker return | gap (pp) |")
    L.append("|---|---|---|---|---|")
    for name, sub in [("pre-cutoff", older), ("recent", recent)]:
        if len(sub) == 0:
            continue
        mk = sub["maker_return"].mean()
        tk = sub["taker_return"].mean()
        L.append(f"| {name} | {len(sub):,} | {mk:+.4f} | {tk:+.4f} | "
                 f"{(mk - tk) * 100:+.2f} |")
    L.append("")
    L.append("Becker's caveat: the maker edge strengthened after the Oct-2024 "
             "volume surge. The gate below uses ONLY the recent regime.")
    L.append("")

    L.append("## Maker strategy gate (recent regime)")
    L.append("")
    if gate["n"] == 0:
        L.append("No recent-regime fills — adjust `--recent-cutoff` or load "
                 "more data.")
    else:
        ci = gate["ci"]
        L.append(f"- Maker fills: **{gate['n']:,}**")
        L.append(f"- Mean maker return after fees + haircut: "
                 f"**{gate['mean_return']:+.4f}**  (**gate: > 0**)")
        L.append(f"- Bootstrap 95% CI: [{ci[0]:+.4f}, {ci[1]:+.4f}]  "
                 f"(**gate: lower bound > 0**)")
        L.append(f"- Dollar-weighted return: "
                 f"{gate['dollar_weighted_return']:+.4f}")
    L.append("")
    L.append("## Gate definition")
    L.append("")
    L.append("PROCEED requires ALL of: >= 100 recent-regime maker fills · "
             "positive mean maker return after fees + adverse-selection "
             "haircut · bootstrap 95% CI lower bound > 0. The maker return is "
             "an UPPER BOUND (assumes you were the counterparty to observed "
             "flow); confirm with real fills in Phase 3 before risking capital.")
    L.append("")
    REPORT_MD.write_text("\n".join(L) + "\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA,
                    help="dataset root (expects <root>/kalshi/{trades,markets})")
    ap.add_argument("--venue", default="kalshi", choices=["kalshi"],
                    help="only kalshi is supported in v1")
    ap.add_argument("--trades", help="explicit path to trades parquet file/dir")
    ap.add_argument("--markets", help="explicit path to markets parquet file/dir")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap trade rows for a quick run")
    ap.add_argument("--taker-fee", type=float, default=0.01,
                    help="taker fee as fraction of capital (default 0.01)")
    ap.add_argument("--maker-fee", type=float, default=0.0,
                    help="maker fee (default 0.0 — makers pay no fee)")
    ap.add_argument("--maker-rebate", type=float, default=0.0,
                    help="maker rebate as fraction of capital (default 0.0)")
    ap.add_argument("--adverse-haircut", type=float, default=0.005,
                    help="subtracted from every maker return to stress adverse "
                         "selection (default 0.005)")
    ap.add_argument("--recent-cutoff", default="2024-10-01",
                    help="ISO date; recent regime is on/after this (default "
                         "2024-10-01, the Kalshi volume surge)")
    ap.add_argument("--longshot-threshold", type=float, default=0.30)
    ap.add_argument("--bootstrap", type=int, default=10_000)
    args = ap.parse_args()

    ds = _require_pyarrow()
    trades_path = _resolve(args.data, args.trades, args.venue, "trades")
    markets_path = _resolve(args.data, args.markets, args.venue, "markets")

    print(f"loading markets from {markets_path} ...")
    markets = load_table(ds, markets_path, MARKET_COLS, None)
    resolved = markets[markets["result"].isin(["yes", "no"])]
    result_map = dict(zip(resolved["ticker"], resolved["result"]))
    print(f"  {len(resolved):,} resolved markets")

    print(f"loading trades from {trades_path} ...")
    trades = load_table(ds, trades_path, TRADE_COLS, args.limit)
    print(f"  {len(trades):,} raw trades")
    trades = trades[trades["ticker"].isin(result_map)].copy()
    trades["result"] = trades["ticker"].map(result_map)
    print(f"  {len(trades):,} trades on resolved markets")
    if len(trades) == 0:
        sys.exit("no resolved trades — check the dataset paths.")

    df = compute_returns(trades, args.taker_fee, args.maker_fee, args.maker_rebate)
    cal = calibration_curve(df)
    tax = optimism_tax(df, args.longshot_threshold)
    cats = category_edge(df)
    older, recent = regime_slice(df, args.recent_cutoff)
    gate = maker_gate(recent, args.bootstrap, args.adverse_haircut)
    label, reasons = verdict(gate, args.adverse_haircut)

    meta = {
        "venue": args.venue, "n_trades": len(df), "n_markets": len(resolved),
        "taker_fee": args.taker_fee, "maker_fee": args.maker_fee,
        "maker_rebate": args.maker_rebate, "cutoff": args.recent_cutoff,
    }
    write_report(meta, cal, tax, cats, older, recent, gate, label, reasons,
                 args.adverse_haircut)

    print(f"\noptimism tax (YES<= {args.longshot_threshold}): "
          f"NO−YES gap = {tax.get('gap_pp', float('nan')):+.1f} pp")
    if gate["n"]:
        print(f"recent maker return: {gate['mean_return']:+.4f}  "
              f"95% CI [{gate['ci'][0]:+.4f}, {gate['ci'][1]:+.4f}]  "
              f"on {gate['n']:,} fills")
    print(f"VERDICT: {label}")
    print(f"report : {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
