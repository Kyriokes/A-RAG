"""Statistics for eval results (PLAN, "Estadística"). Pure numpy and stdlib.

- Every metric is reported with n and a 95 % interval: Wilson for proportions (binary
  per-question values) and a percentile bootstrap for means of non-binary values.
- Comparisons are paired: the difference is measured on the same questions, with a
  bootstrap interval (optionally resampling clusters of questions that share an evidence
  passage), the number of questions fixed and broken, and an exact sign test (McNemar's
  exact test when the values are binary).
- Every bootstrap uses a fixed seed, so two runs over the same data print the same
  intervals.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Z95 = 1.959963984540054  # two-sided 95 % normal quantile


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion; (nan, nan) when n == 0."""
    if n == 0:
        return math.nan, math.nan
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Estimate:
    value: float
    lo: float
    hi: float
    n: int
    method: str  # "wilson", "bootstrap" or "none"

    def to_json(self) -> dict:
        def clean(x: float) -> float | None:
            return None if math.isnan(x) else round(x, 6)

        return {
            "value": clean(self.value),
            "lo": clean(self.lo),
            "hi": clean(self.hi),
            "n": self.n,
            "method": self.method,
        }


def _clean(values: Sequence[float | bool | None]) -> np.ndarray:
    return np.asarray([float(v) for v in values if v is not None], dtype=float)


def bootstrap_mean_ci(
    values: Sequence[float], *, resamples: int, seed: int, alpha: float = 0.05
) -> tuple[float, float]:
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, size=(resamples, x.size))
    means = x[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def estimate(values: Sequence[float | bool | None], *, resamples: int, seed: int) -> Estimate:
    """Mean of the non-None values with a 95 % interval (Wilson if all are 0/1)."""
    x = _clean(values)
    if x.size == 0:
        return Estimate(math.nan, math.nan, math.nan, 0, "none")
    mean = float(x.mean())
    if np.all((x == 0) | (x == 1)):
        lo, hi = wilson(int(x.sum()), int(x.size))
        return Estimate(mean, lo, hi, int(x.size), "wilson")
    lo, hi = bootstrap_mean_ci(x, resamples=resamples, seed=seed)
    return Estimate(mean, lo, hi, int(x.size), "bootstrap")


@dataclass(frozen=True)
class PairedDiff:
    diff: float  # mean of (b - a)
    lo: float
    hi: float
    n: int
    n_clusters: int

    @property
    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0

    def to_json(self) -> dict:
        return {
            "diff": self.diff,
            "lo": self.lo,
            "hi": self.hi,
            "n": self.n,
            "n_clusters": self.n_clusters,
        }


def paired_bootstrap(
    a: Sequence[float],
    b: Sequence[float],
    *,
    resamples: int,
    seed: int,
    clusters: Sequence[str] | None = None,
    alpha: float = 0.05,
) -> PairedDiff:
    """Paired bootstrap of mean(b - a). With `clusters`, whole clusters are resampled
    (questions sharing an evidence passage are not independent)."""
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    if xa.shape != xb.shape:
        raise ValueError("paired_bootstrap: a y b deben tener la misma cantidad de valores")
    d = xb - xa
    if d.size == 0:
        return PairedDiff(math.nan, math.nan, math.nan, 0, 0)
    rng = np.random.default_rng(seed)
    if clusters is None:
        idx = rng.integers(0, d.size, size=(resamples, d.size))
        boots = d[idx].mean(axis=1)
        n_clusters = d.size
    else:
        if len(clusters) != d.size:
            raise ValueError("paired_bootstrap: un cluster por pregunta")
        labels = sorted(set(clusters))
        position = {c: i for i, c in enumerate(labels)}
        sums = np.zeros(len(labels))
        counts = np.zeros(len(labels))
        for value, c in zip(d, clusters, strict=True):
            sums[position[c]] += value
            counts[position[c]] += 1
        idx = rng.integers(0, len(labels), size=(resamples, len(labels)))
        boots = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
        n_clusters = len(labels)
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return PairedDiff(float(d.mean()), float(lo), float(hi), int(d.size), n_clusters)


@dataclass(frozen=True)
class Flips:
    fixed: int  # b better than a (for 0/1 values: failed in a, passes in b)
    broken: int  # b worse than a
    unchanged: int


def flips(a: Sequence[float], b: Sequence[float]) -> Flips:
    fixed = broken = same = 0
    for x, y in zip(a, b, strict=True):
        if y > x:
            fixed += 1
        elif y < x:
            broken += 1
        else:
            same += 1
    return Flips(fixed, broken, same)


def sign_test_p(fixed: int, broken: int) -> float:
    """Exact two-sided sign test on the discordant pairs; McNemar's exact test for 0/1."""
    n = fixed + broken
    if n == 0:
        return 1.0
    k = min(fixed, broken)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def percentile(values: Sequence[float], q: float) -> float | None:
    x = _clean(values)
    return float(np.percentile(x, q)) if x.size else None


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Cohen's kappa between two raters' labels; nan when chance agreement is 1."""
    if len(a) != len(b):
        raise ValueError("cohen_kappa: dos listas de la misma longitud")
    n = len(a)
    if n == 0:
        return math.nan
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    categories = set(a) | set(b)
    expected = sum((list(a).count(c) / n) * (list(b).count(c) / n) for c in categories)
    if expected == 1:
        return math.nan
    return (observed - expected) / (1 - expected)
