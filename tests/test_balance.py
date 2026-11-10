import numpy as np
import pandas as pd

from tpo.balance import accepted_beyond, find_balances, va_overlap_share
from tpo.profile import daily_profiles


def test_va_overlap_share():
    assert va_overlap_share(10, 19, 15, 30) == 0.5
    assert va_overlap_share(10, 19, 0, 100) == 1.0
    assert va_overlap_share(10, 19, 20, 30) == 0.0


def test_accepted_beyond_needs_consecutive_periods():
    lows = np.array([90, 101, 95, 102, 103])
    highs = lows + 5
    assert accepted_beyond(lows, highs, top=100, bottom=0, n=2, consecutive=True) == "up"
    assert accepted_beyond(lows[:4], highs[:4], top=100, bottom=0, n=2, consecutive=True) == ""
    assert accepted_beyond(lows[:4], highs[:4], top=100, bottom=0, n=2, consecutive=False) == "up"


def _bars(days):
    """days: list of (date, [(low, high), ...]) в пунктах."""
    rows = []
    for d, periods in days:
        for i, (l, h) in enumerate(periods):
            rows.append({"date": pd.Timestamp(d), "period": i, "open": l, "high": h,
                         "low": l, "close": h, "volume": 1})
    return pd.DataFrame(rows)


CFG = {
    "data": {"min_periods_full_day": 3},
    "tpo": {"price_row_ticks": 1, "ib_periods": 2, "value_area_pct": 0.7, "tail_min_rows": 2},
    "balance": {"min_days": 3, "va_overlap_min": 0.5,
                "end_rules": ["va_overlap_below_min", "acceptance_beyond_composite_extreme"]},
    "interaction": {"acceptance": {"periods_beyond": 2, "consecutive": True}},
}


def test_find_balances_closes_on_breakout():
    bal = [(100, 110), (102, 108), (101, 109)]
    bars = _bars([
        ("2024-01-02", bal), ("2024-01-03", bal), ("2024-01-04", bal), ("2024-01-05", bal),
        ("2024-01-08", [(108, 115), (115, 125), (116, 126)]),  # 2 периода выше high=110
        ("2024-01-09", [(120, 125)] * 3),
    ])
    daily, prof = daily_profiles(bars, CFG, 1.0)
    comps, _ = find_balances(bars, daily, prof, CFG, 1.0)
    assert len(comps) == 1
    c = comps.iloc[0]
    assert c.n_days == 4 and c.end_reason == "acceptance_up"
    assert c.available_from == pd.Timestamp("2024-01-08")
    assert c.low == 100 and c.high == 110
