import math

import pytest

from evals import stats


def test_wilson_known_values():
    lo, hi = stats.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4)
    assert hi == pytest.approx(0.9433, abs=1e-4)
    lo, hi = stats.wilson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(0.2775, abs=1e-4)
    # The plan: with 25 test questions, 80 % has an interval of about 61 to 91 %.
    lo, hi = stats.wilson(20, 25)
    assert (round(lo, 2), round(hi, 2)) == (0.61, 0.91)
    assert all(math.isnan(x) for x in stats.wilson(0, 0))


def test_estimate_picks_wilson_or_bootstrap():
    binary = stats.estimate([1, 0, 1, 1, None], resamples=1000, seed=1)
    assert (binary.method, binary.n, binary.value) == ("wilson", 4, 0.75)
    mixed = stats.estimate([0.5, 1.0, 0.0, 1.0], resamples=1000, seed=1)
    assert mixed.method == "bootstrap"
    assert mixed.lo <= mixed.value <= mixed.hi
    assert stats.estimate([None], resamples=10, seed=1).n == 0


def test_bootstrap_is_deterministic_with_its_seed():
    values = [0.13, 0.27, 0.91, 0.44, 0.58, 0.02, 0.77, 0.35, 0.66, 0.19]
    a = stats.bootstrap_mean_ci(values, resamples=10_000, seed=1)
    b = stats.bootstrap_mean_ci(values, resamples=10_000, seed=1)
    c = stats.bootstrap_mean_ci(values, resamples=10_000, seed=2)
    assert a == b
    assert a != c


def test_paired_bootstrap():
    a = [0, 0, 1, 1, 0, 1, 0, 0, 1, 0]
    b = [1, 1, 1, 1, 0, 1, 1, 0, 1, 1]
    d1 = stats.paired_bootstrap(a, b, resamples=10_000, seed=1)
    d2 = stats.paired_bootstrap(a, b, resamples=10_000, seed=1)
    assert d1 == d2
    assert d1.diff == pytest.approx(0.4)
    assert d1.lo > 0 and d1.excludes_zero
    same = stats.paired_bootstrap(a, a, resamples=1000, seed=1)
    assert (same.diff, same.lo, same.hi) == (0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        stats.paired_bootstrap([1], [1, 0], resamples=10, seed=1)


def test_cluster_bootstrap_is_wider_when_clusters_are_correlated():
    # Ten clusters of five identical questions: effectively ten observations.
    a = [0.0] * 50
    b = [float(i // 5 % 2) for i in range(50)]
    clusters = [f"c{i // 5}" for i in range(50)]
    plain = stats.paired_bootstrap(a, b, resamples=5000, seed=1)
    clustered = stats.paired_bootstrap(a, b, resamples=5000, seed=1, clusters=clusters)
    assert clustered.n_clusters == 10 and plain.n_clusters == 50
    assert (clustered.hi - clustered.lo) > (plain.hi - plain.lo)


def test_flips_and_exact_p_values():
    f = stats.flips([0, 0, 0, 0, 0, 0, 1, 1, 0], [1, 1, 1, 1, 1, 1, 0, 1, 0])
    assert (f.fixed, f.broken, f.unchanged) == (6, 1, 2)
    assert stats.sign_test_p(6, 1) == pytest.approx(0.125)  # 2 * 8/128
    assert stats.sign_test_p(10, 0) == pytest.approx(2 / 1024)
    assert stats.sign_test_p(0, 0) == 1.0
    assert stats.sign_test_p(3, 3) == 1.0


def test_cohen_kappa():
    assert stats.cohen_kappa(
        ["pass", "pass", "fail", "fail"], ["pass", "fail", "fail", "fail"]
    ) == pytest.approx(0.5)
    assert stats.cohen_kappa(["pass", "fail"], ["pass", "fail"]) == 1.0
    assert math.isnan(stats.cohen_kappa(["pass", "pass"], ["pass", "pass"]))
    # "unknown" from the judge is its own category: a disagreement.
    assert stats.cohen_kappa(["pass", "fail"], ["pass", "unknown"]) < 1.0


def test_percentile():
    assert stats.percentile([1, 2, 3, 4, 5], 50) == 3
    assert stats.percentile([None], 50) is None
