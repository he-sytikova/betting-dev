#!/usr/bin/env python3
"""Exponential recency weighting for training rows.

Older matches contribute less to the loss via sample weights that halve every
``half_life_days``. Weights are normalized to mean 1 so the regularization
strength (alpha) stays comparable to the unweighted fit. Half-life is selected
on validation only, with the same deadband discipline as the other tuning
dimensions: weighting ships only when it beats the unweighted fit by more
than the deadband; otherwise the uniform fit is kept.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# None = uniform (no weighting); 550 ~= 1.5 seasons; 1100 ~= 3 seasons.
HALF_LIFE_GRID = (None, 550, 1100)


def recency_weights(dates, half_life_days, reference=None):
    """Return mean-1 sample weights decaying exponentially with match age.

    ``dates``: array-like of parseable dates/Timestamps.
    ``half_life_days``: None (uniform) or a positive number of days in which
        a weight halves.
    ``reference``: Timestamp the age is measured from; defaults to the latest
        date in ``dates`` (i.e. the most recent training match has weight 1
        before normalization).
    """
    if half_life_days is None:
        return np.ones(len(dates), dtype=float)
    if not np.isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError(f"half_life_days must be a positive number or None, got {half_life_days}")
    when = pd.to_datetime(pd.Series(dates), errors="coerce")
    if when.isna().any():
        raise ValueError("recency_weights got unparseable dates")
    ref = pd.Timestamp(reference) if reference is not None else when.max()
    age_days = (ref - when).dt.total_seconds().to_numpy(float) / 86400.0
    if (age_days < 0).any():
        raise ValueError("reference is earlier than some dates; weights would upweight the future")
    w = np.power(0.5, age_days / float(half_life_days))
    return w / w.mean()
