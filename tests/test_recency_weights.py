#!/usr/bin/env python3
"""Тесты модуля recency weighting (scripts/models/recency.py)."""
import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


recency = _load("recency", "scripts/models/recency.py")


class RecencyWeightsTest(unittest.TestCase):
    def test_uniform_when_half_life_is_none(self):
        w = recency.recency_weights(pd.to_datetime(["2020-01-01", "2024-01-01"]), None)
        np.testing.assert_array_equal(w, np.ones(2))

    def test_weight_halves_every_half_life(self):
        ref = pd.Timestamp("2024-01-01")
        dates = pd.to_datetime([ref, ref - pd.Timedelta(days=730), ref - pd.Timedelta(days=1460)])
        w = recency.recency_weights(dates, 730, reference=ref)
        self.assertAlmostEqual(w[0] / w[1], 2.0, places=6)
        self.assertAlmostEqual(w[0] / w[2], 4.0, places=6)

    def test_weights_are_mean_one_and_monotone(self):
        dates = pd.date_range("2018-01-01", "2024-01-01", periods=100)
        w = recency.recency_weights(dates, 550)
        self.assertAlmostEqual(float(w.mean()), 1.0, places=9)
        self.assertTrue(np.all(np.diff(w) >= 0))  # старее -> меньше вес (даты по возрастанию)
        self.assertLess(w[0], w[-1])

    def test_default_reference_is_latest_date(self):
        dates = pd.to_datetime(["2023-01-01", "2024-01-01"])
        w = recency.recency_weights(dates, 365)
        self.assertAlmostEqual(w.max(), w[-1])  # свежайший матч — максимальный вес

    def test_rejects_invalid_arguments(self):
        dates = pd.to_datetime(["2024-01-01"])
        for bad in (0, -10, float("nan")):
            with self.assertRaises(ValueError):
                recency.recency_weights(dates, bad)
        with self.assertRaises(ValueError):
            recency.recency_weights(["not-a-date"], 550)
        with self.assertRaises(ValueError):
            recency.recency_weights(pd.to_datetime(["2024-01-01"]), 550,
                                    reference=pd.Timestamp("2020-01-01"))

    def test_weighted_fit_recovers_recent_signal(self):
        """На дрейфующем процессе взвешенный Poisson точнее на будущем окне."""
        from sklearn.linear_model import PoissonRegressor
        rng = np.random.default_rng(7)
        n = 4000
        dates = pd.date_range("2020-01-01", periods=n, freq="D")
        drift = np.linspace(-0.5, 0.5, n)  # истинный эффект признака растёт со временем
        x = rng.normal(size=n)
        mu = np.exp(0.2 + drift * x)
        y = rng.poisson(mu)
        # Валидация — самые свежие 20% (дрейф близок к drift[-1]).
        cut = int(n * 0.8)
        w = recency.recency_weights(dates[:cut], 365)
        plain = PoissonRegressor(alpha=0.0, max_iter=500).fit(x[:cut, None], y[:cut])
        weighted = PoissonRegressor(alpha=0.0, max_iter=500).fit(x[:cut, None], y[:cut], sample_weight=w)

        def deviance(model):
            pred = np.clip(model.predict(x[cut:, None]), 1e-8, None)
            yy = y[cut:]
            return float(2 * np.mean(np.where(yy > 0, yy * np.log(np.maximum(yy, 1e-12) / pred) - (yy - pred), pred)))

        self.assertLess(deviance(weighted), deviance(plain))


if __name__ == "__main__":
    unittest.main()
