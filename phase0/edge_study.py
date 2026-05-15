"""Phase 0 edge-existence study for the favorite-longshot bias thesis.

Reads phase0/data/events.csv (produced by collect.py), tests whether backing
favorites produces a fee-surviving edge, and writes a verdict to
phase0/REPORT.md.

The Phase 0 gate (from the blueprint, section 7) passes only when ALL of:
  * the strategy has >= 100 trades
  * mean net return after fees is positive (the EV check)
  * the bootstrap 95% CI lower bound of per-trade CLV is > 0 (the edge check)
  * the data is REAL (not synthetic)

The confidence interval is taken on CLV, not on net return: CLV has far
lower variance than binary-outcome P&L, so an edge is detectable in ~100
trades instead of thousands. Net return only has to be positive on average.

Synthetic data can only return 'PIPELINE OK' — never 'PROCEED'.

Usage:
    python edge_study.py [--data DATA] [--fee FEE] [--fav-threshold P]
                         [--entry {opening,closing}] [--bootstrap N]
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats

from oddslib import bootstrap_ci, clv, net_return

DATA_CSV = Path(__file__).parent / "data" / "events.csv"
REPORT_MD = Path(__file__).parent / "REPORT.md"


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"no data at {path}\n"
            "run one of:\n"
            "  python collect.py synthetic            # offline pipeline test\n"
            "  python collect.py snapshot --sport ... # then: build"
        )
    rows = []
    with path.open() as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "event_id": r["event_id"],
                "selection": r["selection"],
                "opening_price": float(r["opening_price"]),
                "closing_price": float(r["closing_price"]),
                "outcome": int(r["outcome"]),
                "data_source": r["data_source"],
            })
    if not rows:
        raise SystemExit(f"{path} is empty")
    return rows


def calibration_table(rows: list[dict], entry: str,
                       n_bins: int = 10) -> list[dict]:
    """Bucket selections by entry price; compare price to realized win rate.

    Favorite-longshot bias shows up as: low-price buckets win LESS than their
    price (longshots overpriced) and high-price buckets win MORE than their
    price (favorites underpriced).
    """
    price_key = f"{entry}_price"
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    table = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        bucket = [r for r in rows if lo <= r[price_key] < hi]
        if not bucket:
            continue
        prices = np.array([r[price_key] for r in bucket])
        wins = np.array([r["outcome"] for r in bucket])
        table.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "n": len(bucket),
            "mean_price": float(prices.mean()),
            "win_rate": float(wins.mean()),
            "gap": float(wins.mean() - prices.mean()),  # +ve => underpriced
        })
    return table


def run_strategy(rows: list[dict], fav_threshold: float, fee: float,
                 entry: str) -> dict:
    """Back every favorite priced at or above `fav_threshold`.

    Entry price is the opening or closing market price depending on `entry`.
    Returns per-trade net return series, CLV series, and summary stats.
    """
    price_key = f"{entry}_price"
    trades = [r for r in rows if r[price_key] >= fav_threshold]

    returns, clvs = [], []
    for r in trades:
        returns.append(net_return(r[price_key], r["outcome"], fee))
        clvs.append(clv(r["opening_price"], r["closing_price"]))
    returns = np.array(returns)
    clvs = np.array(clvs)

    out = {"n": len(trades), "entry": entry, "fav_threshold": fav_threshold,
           "fee": fee, "returns": returns, "clvs": clvs}
    if len(trades) == 0:
        return out

    mean = float(returns.mean())
    out["mean_return"] = mean
    out["std_return"] = float(returns.std(ddof=1)) if len(trades) > 1 else float("nan")
    out["return_ci"] = bootstrap_ci(returns)
    out["sharpe_like"] = mean / out["std_return"] if out["std_return"] else float("nan")
    out["win_rate"] = float((returns > 0).mean())

    # CLV is the primary edge detector: low variance, so a tight CI.
    out["mean_clv"] = float(clvs.mean())
    out["clv_ci"] = bootstrap_ci(clvs)
    out["beat_close_rate"] = float((clvs > 0).mean())
    # One-sided t-test that mean CLV > 0.
    if len(trades) > 1:
        t_stat, p_two = stats.ttest_1samp(clvs, 0.0)
        out["clv_t_stat"] = float(t_stat)
        out["clv_p_value"] = float(p_two / 2.0 if t_stat > 0 else 1.0 - p_two / 2.0)
    else:
        out["clv_t_stat"] = float("nan")
        out["clv_p_value"] = float("nan")
    return out


def verdict(strat: dict, data_source: str) -> tuple[str, list[str]]:
    """Return (verdict_label, reasons)."""
    reasons = []
    n_ok = strat["n"] >= 100
    if not n_ok:
        reasons.append(f"only {strat['n']} trades (need >= 100)")
    if strat["n"] == 0:
        return "FAIL", reasons

    ev_ok = strat["mean_return"] > 0
    if not ev_ok:
        reasons.append(f"mean net return {strat['mean_return']:+.4f} is not "
                       f"positive (EV check)")
    clv_lo = strat["clv_ci"][0]
    clv_ci_ok = clv_lo > 0
    if not clv_ci_ok:
        reasons.append(f"CLV bootstrap 95% CI lower bound {clv_lo:+.4f} is not "
                       f"> 0 (edge check)")

    gate_ok = n_ok and ev_ok and clv_ci_ok
    if not gate_ok:
        return "FAIL", reasons or ["statistical gate not met"]

    if data_source == "synthetic":
        return "PIPELINE OK", [
            "all statistical checks passed on SYNTHETIC data — the analysis "
            "pipeline works, but this proves nothing about a real edge",
            "collect real data (collect.py snapshot + build) and re-run before "
            "trusting any 'PROCEED' verdict",
        ]
    return "PROCEED", [
        "statistical gate met on real data — a fee-surviving favorite-longshot "
        "edge is demonstrated; proceed to Phase 1",
    ]


def write_report(rows: list[dict], cal: list[dict], strat: dict,
                  label: str, reasons: list[str]) -> None:
    ds = rows[0]["data_source"]
    n_events = len({r["event_id"] for r in rows})
    lines = []
    lines.append("# Phase 0 — Edge Existence Study: REPORT")
    lines.append("")
    lines.append("**Thesis:** favorite-longshot bias — longshots are "
                 "systematically overpriced, favorites underpriced. Strategy: "
                 "back favorites.")
    lines.append("")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Data source: `{ds}`")
    lines.append(f"- Events: {n_events}  ·  selection rows: {len(rows)}")
    lines.append(f"- Entry price: {strat['entry']}  ·  "
                 f"favorite threshold: {strat['fav_threshold']:.2f}  ·  "
                 f"fee: {strat['fee']:.3f}")
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(f"### {label}")
    lines.append("")
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")
    if ds == "synthetic":
        lines.append("> Synthetic data is a pipeline self-test. A real "
                     "`PROCEED` requires real collected data.")
        lines.append("")

    lines.append("## Calibration (favorite-longshot bias check)")
    lines.append("")
    lines.append(f"`gap` = realized win rate − mean {strat['entry']} price. "
                 "Negative in low buckets and positive in high buckets "
                 "confirms the bias.")
    lines.append("")
    lines.append(f"| {strat['entry']} price | n | mean price | win rate | gap |")
    lines.append("|---|---|---|---|---|")
    for b in cal:
        lines.append(f"| {b['range']} | {b['n']} | {b['mean_price']:.3f} "
                     f"| {b['win_rate']:.3f} | {b['gap']:+.3f} |")
    lines.append("")

    lines.append("## Strategy results (back favorites)")
    lines.append("")
    if strat["n"] == 0:
        lines.append("No trades met the favorite threshold — lower "
                      "`--fav-threshold` or collect more data.")
    else:
        clv_ci = strat["clv_ci"]
        ret_ci = strat["return_ci"]
        lines.append("### Closing Line Value — primary edge detector")
        lines.append("")
        lines.append(f"- Mean CLV (opening → closing): "
                     f"**{strat['mean_clv']:+.4f}**")
        lines.append(f"- Bootstrap 95% CI on CLV: "
                     f"[{clv_ci[0]:+.4f}, {clv_ci[1]:+.4f}]  "
                     f"(**gate: lower bound must be > 0**)")
        lines.append(f"- One-sided t-test p-value (mean CLV > 0): "
                     f"{strat['clv_p_value']:.4g}")
        lines.append(f"- Beat-close rate: {strat['beat_close_rate']:.3f}")
        lines.append("")
        lines.append("Positive CLV means the closing line moved toward the "
                     "favorite after entry — the market itself corrected the "
                     "opening underpricing. CLV has low variance, so this is "
                     "the statistically reliable signal.")
        lines.append("")
        lines.append("### Net return — the EV check")
        lines.append("")
        lines.append(f"- Trades: **{strat['n']}**")
        lines.append(f"- Mean net return (after fees): "
                     f"**{strat['mean_return']:+.4f}** per $1 staked  "
                     f"(**gate: must be > 0**)")
        lines.append(f"- Bootstrap 95% CI on net return: "
                     f"[{ret_ci[0]:+.4f}, {ret_ci[1]:+.4f}]  "
                     f"(informational — binary outcomes make this wide)")
        lines.append(f"- Return std dev: {strat['std_return']:.4f}  ·  "
                     f"per-trade Sharpe-like: {strat['sharpe_like']:.3f}")
        lines.append(f"- Trade win rate: {strat['win_rate']:.3f}")
    lines.append("")
    lines.append("## Gate definition")
    lines.append("")
    lines.append("PROCEED requires ALL of: >= 100 trades · positive mean net "
                 "return (EV check) · CLV bootstrap 95% CI lower bound > 0 "
                 "(edge check) · real (non-synthetic) data. Anything else is "
                 "FAIL — pivot the edge source or venue and repeat Phase 0. "
                 "Do not start Phase 1.")
    lines.append("")

    REPORT_MD.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DATA_CSV)
    parser.add_argument("--fee", type=float, default=0.02,
                        help="round-trip fee as a fraction of stake (default 0.02)")
    parser.add_argument("--fav-threshold", type=float, default=0.60,
                        help="minimum market price to count as a favorite "
                             "(default 0.60)")
    parser.add_argument("--entry", choices=["opening", "closing"],
                        default="opening",
                        help="which market price you transact at (default opening)")
    parser.add_argument("--bootstrap", type=int, default=10_000)
    args = parser.parse_args()

    rows = load_events(args.data)
    cal = calibration_table(rows, args.entry)
    strat = run_strategy(rows, args.fav_threshold, args.fee, args.entry)
    label, reasons = verdict(strat, rows[0]["data_source"])
    write_report(rows, cal, strat, label, reasons)

    # Console summary.
    print(f"data source : {rows[0]['data_source']}")
    print(f"trades      : {strat['n']}")
    if strat["n"]:
        print(f"mean CLV    : {strat['mean_clv']:+.4f}  "
              f"(95% CI [{strat['clv_ci'][0]:+.4f}, {strat['clv_ci'][1]:+.4f}])")
        print(f"mean return : {strat['mean_return']:+.4f}  (EV check)")
    print(f"VERDICT     : {label}")
    print(f"report      : {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
