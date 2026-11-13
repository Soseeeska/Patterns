import numpy as np
import pandas as pd

from tpo.grey_zones import EDGE, GAP, INSIDE, PARTIAL, classify_vs_normal, grey_until, history_on


def test_classify_positions():
    n_lo, n_hi = np.array([100.0, 120.0]), np.array([105.0, 125.0])
    assert classify_vs_normal(110, 112, n_lo, n_hi, 0.25)[0] == GAP
    assert classify_vs_normal(130, 132, n_lo, n_hi, 0.25)[0] == EDGE
    assert classify_vs_normal(101, 104, n_lo, n_hi, 0.25) == (INSIDE, 1.0)
    pos, cov = classify_vs_normal(104, 106, n_lo, n_hi, 0.25)
    assert pos == PARTIAL and 0 < cov < 1
    assert classify_vs_normal(110, 112, np.array([]), np.array([]), 0.25)[0] == EDGE


def test_grey_until_and_history():
    d = [pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-10")]
    assert grey_until(2, d, 2) is None                       # формирование 2 дня — не серая
    assert pd.Timestamp(grey_until(1, d, 2)) == pd.Timestamp("2024-01-06")
    assert pd.isna(grey_until(1, [], 2))                     # без подтверждений — серая всё время
    assert history_on(1, d, pd.Timestamp("2024-01-05")) == 1  # подтверждение в тот же день не считается
    assert history_on(1, d, pd.Timestamp("2024-01-06")) == 2
