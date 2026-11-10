import pandas as pd

from tpo.intraday import run_tests

CFG = {"intraday": {"reaction_bars_after": 2},
       "interaction": {"touch_tolerance_ticks": 0}}


def _bars(rows):
    """rows: (low, high, close) по порядку, все в один день."""
    d = pd.Timestamp("2024-01-02")
    return pd.DataFrame([{"ts": i, "date": d, "period": i, "low": l, "high": h, "close": c}
                         for i, (l, h, c) in enumerate(rows)])


def _band(level):
    return pd.DataFrame([{"zone_id": "z", "composite_id": 0, "level_type": "VAH", "lo": level, "hi": level,
                          "composite_days": 3, "available_from": pd.Timestamp("2024-01-02"),
                          "composite_end": pd.Timestamp("2023-12-29")}])


def test_hold_then_break_and_flip():
    bars = _bars([
        (110, 115, 112),   # над уровнем 100
        (99, 108, 104),    # касание сверху, закрылись над
        (103, 107, 106),   # бар целиком над → удержание
        (98, 104, 99),     # касание, close под (ложный?)
        (95, 101, 97),     # второй close под → пробой, флип
        (90, 96, 92),
        (93, 100, 95),     # касание снизу (зона уже сопротивление)
        (90, 96, 91),      # целиком под → удержание сопротивления
    ])
    t = run_tests(bars, _band(100.0), CFG, 1.0)
    assert list(t.outcome) == ["hold", "break", "hold"]
    assert list(t.role) == ["support", "support", "resistance"]
    assert list(t.zone_state) == ["original", "original", "flipped"]


def test_single_close_beyond_is_false_break():
    bars = _bars([
        (110, 115, 112),
        (98, 108, 99),     # close под уровнем
        (99, 106, 104),    # но следующий закрылся обратно над
        (103, 107, 106),   # целиком над → удержание
    ])
    t = run_tests(bars, _band(100.0), CFG, 1.0)
    assert list(t.outcome) == ["hold"] and bool(t.false_break.iloc[0])
