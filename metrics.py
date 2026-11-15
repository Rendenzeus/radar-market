"""Small statistics helpers for judging a trade record honestly."""

from __future__ import annotations

import math
import random


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def win_rate_interval(wins: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper-Pearson) confidence interval for a win rate."""
    if n == 0:
        return 0.0, 1.0
    alpha = (1 - level) / 2

    def solve(f, lo=0.0, hi=1.0) -> float:
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid):
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2

    lower = 0.0 if wins == 0 else solve(lambda p: 1 - _binom_cdf(wins - 1, n, p) >= alpha)
    upper = 1.0 if wins == n else solve(lambda p: _binom_cdf(wins, n, p) <= alpha)
    return lower, upper


def bootstrap_mean_interval(values: list[float], level: float = 0.95, resamples: int = 10000, seed: int = 1) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean."""
    if not values:
        return 0.0, 0.0
    rnd = random.Random(seed)
    n = len(values)
    means = sorted(sum(rnd.choice(values) for _ in range(n)) / n for _ in range(resamples))
    a = (1 - level) / 2
    return means[int(a * resamples)], means[min(resamples - 1, int((1 - a) * resamples))]


def max_drawdown(results: list[float]) -> float:
    """Largest peak-to-trough fall of the cumulative result (same unit, e.g. R)."""
    peak = equity = worst = 0.0
    for r in results:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def longest_losing_streak(results: list[float]) -> int:
    best = run = 0
    for r in results:
        run = run + 1 if r <= 0 else 0
        best = max(best, run)
    return best
