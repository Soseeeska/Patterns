import pandas as pd

from tpo.zones18 import first_tests

DAY = pd.Series({"date": pd.Timestamp("2024-06-05"), "y_high": 120.0, "y_low": 80.0, "y_vah": 110.0, "y_val": 90.0,
                 "y_poc": 100.0, "ib_avg20": 10.0})


def bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"]).assign(period=range(len(rows)))


def test_support_hold_and_2r_win():
    # open над зоной 100–102, B касается, C уходит вверх на цель 2R (R = 2 + 2 = 4, цель 102 + 8 = 110)
    g = bars([(106, 107, 104, 105), (105, 105.5, 101.5, 104), (104, 111, 103.5, 110)] + [(110, 111, 109, 110)] * 10)
    ev = first_tests(DAY, g, pd.DataFrame({"lo": [100.0], "hi": [102.0]}), 0.25)
    assert len(ev) == 1
    e = ev[0]
    assert (e["t"], e["role"], e["outcome"], e["r"], e["range_pos"]) == (1, "поддержка", "hold", 2.0, "за экстремумом")


def test_break_two_closes_and_stop_on_touch_bar():
    # сопротивление 104–105 снизу: касание в A с проколом за стоп (107), два close над зоной — пробой
    g = bars([(102, 108, 101, 106), (106, 109, 105.5, 107)] + [(107, 108, 106, 107)] * 11)
    ev = first_tests(DAY, g, pd.DataFrame({"lo": [104.0], "hi": [105.0]}), 0.25)
    e = ev[0]
    assert (e["t"], e["role"], e["outcome"], e["r"], e["confl"]) == (0, "сопротивление", "break", -1.0, "нет")


def test_open_inside_zone_waits_for_side():
    # open внутри зоны: теста нет, пока close не выйдет из неё
    g = bars([(100.5, 101, 100, 100.5)] * 13)
    assert first_tests(DAY, g, pd.DataFrame({"lo": [100.0], "hi": [101.0]}), 0.25) == []
