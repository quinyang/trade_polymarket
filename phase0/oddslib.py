"""Shared odds / probability / statistics helpers for the Phase 0 edge study.

Pure functions only — no I/O, no global state. Imported by collect.py and
edge_study.py.
"""

from __future__ import annotations

import numpy as np


# --------------------------------------------------------------------------
# Odds <-> probability conversion
# --------------------------------------------------------------------------

def decimal_to_prob(decimal_odds: float) -> float:
    """Implied probability from decimal odds (e.g. 2.50 -> 0.40)."""
    return 1.0 / decimal_odds


def american_to_prob(american_odds: float) -> float:
    """Implied probability from American odds (e.g. -150 -> 0.60)."""
    if american_odds < 0:
        return -american_odds / (-american_odds + 100.0)
    return 100.0 / (american_odds + 100.0)


def prob_to_decimal(prob: float) -> float:
    """Decimal odds from a probability."""
    return 1.0 / prob


# --------------------------------------------------------------------------
# Vig removal
# --------------------------------------------------------------------------

def devig_proportional(probs: np.ndarray) -> np.ndarray:
    """Remove bookmaker margin by proportional normalisation.

    Raw implied probabilities sum to >1 because of the vig. The proportional
    method scales them so they sum to 1. It is the simplest devig and is
    adequate for Phase 0; it does NOT correct for favorite-longshot distortion
    (that is exactly the bias we are trying to measure).
    """
    probs = np.asarray(probs, dtype=float)
    total = probs.sum()
    if total <= 0:
        raise ValueError("probabilities must sum to a positive number")
    return probs / total


def overround(probs: np.ndarray) -> float:
    """Bookmaker overround (vig). 1.05 means a 5% margin."""
    return float(np.asarray(probs, dtype=float).sum())


# --------------------------------------------------------------------------
# Closing Line Value
# --------------------------------------------------------------------------

def clv(entry_price: float, closing_price: float) -> float:
    """CLV for a YES (buy) position.

    Positive => the closing price is higher than your entry price, i.e. you
    bought cheaper than the market's final, best-informed estimate. This is
    the high-signal edge detector from the blueprint (section 3G).
    """
    return closing_price - entry_price


# --------------------------------------------------------------------------
# Trade economics
# --------------------------------------------------------------------------

def net_return(entry_price: float, outcome: int, fee_rate: float) -> float:
    """Net return per $1 staked on a YES contract that pays $1 if it resolves YES.

    Buy at `entry_price`, so $1 buys (1 / entry_price) contracts.
      - if outcome == 1: receive (1 / entry_price) dollars
      - if outcome == 0: receive 0
    `fee_rate` is charged as a fraction of the stake (round-trip).
    """
    gross = outcome / entry_price
    return gross - 1.0 - fee_rate


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def bootstrap_ci(
    sample: np.ndarray,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean of `sample`.

    Returns (lower, upper) at the (1 - alpha) confidence level.
    """
    sample = np.asarray(sample, dtype=float)
    if sample.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, sample.size, size=(n_boot, sample.size))
    means = sample[idx].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2.0))
    hi = float(np.quantile(means, 1.0 - alpha / 2.0))
    return (lo, hi)
