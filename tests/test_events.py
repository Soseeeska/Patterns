import pandas as pd

from tests.test_balance import CFG, _bars
from tpo.balance import find_balances
from tpo.day_type import classify_days
from tpo.events import build_events
from tpo.profile import daily_profiles

CFG_EV = {
    **CFG,
    "zones": {"levels": ["VAH", "VAL", "POC"]},
    "interaction": {**CFG["interaction"], "touch_tolerance_ticks": 0,
                    "opposite_boundary_horizon_days": [0, 1, 3]},
    "day_type": {"avg_range_lookback_days": 20, "min_extension_share_of_ib": 0.15,
                 "non_trend_range_vs_avg_max": 0.6, "trend_range_vs_ib_min": 2.0,
                 "trend_close_extreme_share": 0.15, "trend_min_va_share_of_range": 0.45,
                 "trend_max_tpo_per_row": 9,
                 "double_distribution": {"gap_max_tpo_per_row": 2, "min_single_print_gap_ticks": 4,
                                         "min_gap_share_of_range": 0.1},
                 "neutral": {"center_close_band": [0.25, 0.75]}},
    "profile_shape": {"p_b_mass_center_min": 0.55, "thin_max_tpo_per_row": 3},
}


def test_events_no_lookahead_and_outcomes():
    bal = [(100, 110), (102, 108), (101, 109)]
    bars = _bars([
        ("2024-01-02", bal), ("2024-01-03", bal), ("2024-01-04", bal),
        ("2024-01-05", [(108, 115), (115, 125), (116, 126)]),   # пробой вверх, закрывает баланс
        ("2024-01-08", [(120, 125), (118, 122), (119, 124)]),   # далеко от зон
        ("2024-01-09", [(112, 118), (106, 112), (100, 104)]),   # сверху вниз сквозь VAH
    ])
    daily, prof = daily_profiles(bars, CFG_EV, 1.0)
    days = classify_days(daily, prof, CFG_EV, 1.0)
    comps, _ = find_balances(bars, daily, prof, CFG_EV, 1.0)
    rolls = pd.DataFrame({"roll_date": pd.to_datetime([])})
    ev = build_events(bars, days, comps, rolls, CFG_EV, 1.0)
    avail = comps.set_index("composite_id").available_from
    assert (ev.date >= ev.composite_id.map(avail)).all()
    assert not (ev.date < pd.Timestamp("2024-01-05")).any()
    bo = ev[ev.date == pd.Timestamp("2024-01-05")]
    assert bo.breakout_day.all()
    vah = ev[(ev.date == pd.Timestamp("2024-01-09")) & (ev.level_type == "VAH")].iloc[0]
    assert vah.approach == "above"
    # касание в периоде 1; целиком ниже VAH только период 2 → одного периода мало
    assert vah.first_touch_period == 1
    assert vah.outcome == "rejection"
    assert vah.touch_number == 2 and not vah.breakout_day


def test_poc_close_mode_entering_only():
    cfg = {**CFG_EV, "interaction": {**CFG_EV["interaction"],
                                     "acceptance": {**CFG_EV["interaction"]["acceptance"],
                                                    "mode": "poc_close", "poc_tolerance_ticks": 0}}}
    bal = [(100, 110), (102, 108), (101, 109)]
    bars = _bars([
        ("2024-01-02", bal), ("2024-01-03", bal), ("2024-01-04", bal),
        ("2024-01-05", [(108, 115), (115, 125), (116, 126)]),
        ("2024-01-08", [(120, 125), (118, 122), (119, 124)]),
        ("2024-01-09", [(112, 118), (106, 112), (100, 104)]),   # вход сверху, close 104
    ])
    daily, prof = daily_profiles(bars, cfg, 1.0)
    days = classify_days(daily, prof, cfg, 1.0)
    comps, _ = find_balances(bars, daily, prof, cfg, 1.0)
    rolls = pd.DataFrame({"roll_date": pd.to_datetime([])})
    ev = build_events(bars, days, comps, rolls, cfg, 1.0)
    day = ev[ev.date == pd.Timestamp("2024-01-09")]
    # VAL и POC при подходе сверху — не вход в композит, их нет
    assert set(day.level_type) == {"VAH"}
    r = day.iloc[0]
    assert r.approach == "above"
    assert r.outcome == ("acceptance" if 104 <= r.comp_poc else "rejection")
    assert r.comp_poc >= 104 and r.outcome == "acceptance"
