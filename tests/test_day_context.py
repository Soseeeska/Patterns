import numpy as np
import pandas as pd

from tpo.day_context import open_location, single_runs, spike


def test_spike_up_and_none():
    g = pd.DataFrame({"period": [0, 1, 2], "high": [10.0, 11.0, 14.0], "low": [9.0, 9.5, 10.5]})
    assert spike(g, 2.0) == ("up", 11.0, 14.0)
    assert spike(g, 4.0)[0] == "none"


def test_single_runs_min_length():
    c = np.array([1, 1, 3, 1, 1, 1, 2, 1])
    assert single_runs(c, 100, 1, 2) == [(100, 101), (103, 105)]


def test_open_location():
    p = pd.Series({"high": 110, "vah": 105, "val": 100, "low": 95})
    assert [open_location(o, p) for o in (111, 107, 102, 97, 90)] == \
        ["выше High", "VAH–High", "внутри VA", "Low–VAL", "ниже Low"]
