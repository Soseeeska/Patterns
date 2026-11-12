import pandas as pd

from tpo.primary_zones import build_primary_zones, zone_tests

D = pd.date_range("2024-01-01", periods=20, freq="D")


def _days(rows):
    return pd.DataFrame([{"date": D[i], "lo": lo, "hi": hi, "ok": ok} for i, (lo, hi, ok) in enumerate(rows)])


def test_forming_fixing_and_confirmation():
    zs = build_primary_zones(_days([
        (100, 110, True),    # старт зоны
        (104, 112, True),    # расширение до 100..112
        (101, 109, False),   # цена в зоне, день не зачётный — без изменений
        (200, 210, False),   # ушли — фиксация, новой зоны нет (не зачётный)
        (300, 310, True),    # новая зона
        (102, 111, True),    # ушли из 300..310 → фиксация; подтверждение 1-й зоны
        (103, 108, True),    # ещё подтверждение (новая зона не начинается)
    ]), 0.5, 40)
    first, second = zs
    assert (first.lo, first.hi, first.days, first.fixed_on) == (100, 112, [D[0], D[1]], D[3])
    assert first.confirmations == [D[5], D[6]]
    assert (second.lo, second.hi, second.fixed_on, second.confirmations) == (300, 310, D[5], [])


def test_width_cap_keeps_zone_but_does_not_extend():
    zs = build_primary_zones(_days([(0, 30, True), (10, 45, True), (500, 510, False)]), 0.5, 40)
    assert len(zs) == 1 and (zs[0].lo, zs[0].hi, len(zs[0].days)) == (0, 30, 1)


def test_unfixed_zone_at_end():
    zs = build_primary_zones(_days([(0, 10, True), (2, 9, True)]), 0.5, 40)
    assert zs[0].fixed_on is None and len(zs[0].days) == 2


def test_zone_tests_roles_and_flip():
    daily = pd.DataFrame({"date": D[:4],
                          "open": [120, 115, 90, 95], "high": [125, 118, 102, 99],
                          "low": [105, 95, 85, 90], "close": [115, 96, 92, 97]})
    zones = pd.DataFrame([{"zone_id": 1, "lo": 100, "hi": 110, "available_from": D[0], "conf_dates": [D[1]]}])
    t = zone_tests(daily, zones, 0)
    assert list(t.role) == ["support", "support", "resistance"]
    assert list(t.result) == ["hold", "break", "hold"]
    assert list(t.after_break) == [False, False, True]
    assert list(t.confirmations_before) == [0, 0, 1]
