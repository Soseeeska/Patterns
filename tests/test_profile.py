import numpy as np

from tpo.profile import build_profile, merge_profiles, poc_index, tail_rows, value_area


def test_build_profile_counts():
    # периоды: [0..4], [2..6], [3..3]
    p = build_profile(np.array([0, 2, 3]), np.array([4, 6, 3]))
    assert p.low == 0 and p.high == 6
    assert p.counts.tolist() == [1, 1, 2, 3, 2, 1, 1]


def test_poc_tie_closest_to_center():
    p = build_profile(np.array([0, 0, 6, 6]), np.array([0, 0, 8, 8]))
    # max=2 в строках 0 и 6..8; центр диапазона = 4 -> ближе строка 6
    assert p.price_of(poc_index(p)) == 6


def test_value_area_dalton():
    counts = np.array([1, 1, 2, 4, 6, 5, 3, 1, 1, 1])
    from tpo.profile import Profile
    p = Profile(100, counts, 1)
    poc, val, vah = value_area(p, 0.70)
    # total=25, target=17.5. POC=104 (6).
    # шаг1: up(105,106)=8 > dn(102,103)=6 -> +8 -> 14
    # шаг2: up(107,108)=2 < dn(102,103)=6 -> +6 -> 20 >= 17.5
    assert (poc, val, vah) == (104, 102, 106)


def test_tail_rows():
    c = np.array([1, 1, 3, 2, 1])
    assert tail_rows(c, from_top=False) == 2
    assert tail_rows(c, from_top=True) == 1


def test_merge_profiles():
    a = build_profile(np.array([0]), np.array([2]))
    b = build_profile(np.array([2]), np.array([4]))
    m = merge_profiles([a, b])
    assert m.low == 0 and m.counts.tolist() == [1, 1, 2, 1, 1]


def test_roll_dates_tuesday_before_third_friday():
    import pandas as pd
    from tpo.rolls import roll_dates
    d = roll_dates(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"))
    assert [x.strftime("%m-%d") for x in d] == ["03-12", "06-18", "09-17", "12-17"]


def test_back_adjust_shifts_only_before_roll():
    import pandas as pd
    from tpo.rolls import back_adjust
    bars = pd.DataFrame({"date": pd.to_datetime(["2024-03-11", "2024-03-12"]),
                         "open": [100.0, 150.0], "high": [101.0, 151.0],
                         "low": [99.0, 149.0], "close": [100.0, 150.0]})
    rolls = pd.DataFrame({"roll_date": [pd.Timestamp("2024-03-12")], "spread": [50.0]})
    out = back_adjust(bars, rolls)
    assert out["close"].tolist() == [150.0, 150.0]
    assert out["close_raw"].tolist() == [100.0, 150.0]
