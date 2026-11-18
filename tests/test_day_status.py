import pandas as pd

from tpo.day_status import naked_pocs


def test_naked_pocs_untouched_and_contract():
    d = pd.DataFrame({
        "date": pd.to_datetime(["2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14"]),
        "short_day": False, "roll_day": [False, False, False, True, False],
        "poc": [100.0, 110.0, 120.0, 200.0, 205.0],
        "low": [95.0, 108.0, 99.0, 195.0, 201.0], "high": [105.0, 115.0, 125.0, 210.0, 209.0],
    })
    n = naked_pocs(d)
    assert list(n[pd.Timestamp("2024-06-12")]) == [100.0]          # 110 — вчерашний POC, отдельная цель
    assert list(n[pd.Timestamp("2024-06-13")]) == []               # 12-й задел 100 и 110; ролл 13-го — новый контракт
    assert list(n[pd.Timestamp("2024-06-14")]) == []               # 200 — вчерашний; старый контракт не берётся
