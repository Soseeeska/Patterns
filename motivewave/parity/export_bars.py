"""Бары для проверки Java-движка (motivewave/) против Python.

Пишет back-adjusted 30m-бары одним рядом, как их увидит индикатор в MotiveWave:
RTH-бары до начала ETH-выгрузки + ETH+RTH-бары после.
Формат: epoch_ms (начало бара, UTC),open,high,low,close

Запуск (из корня):  python -m motivewave.parity.export_bars out.csv
"""
from __future__ import annotations

import sys

import pandas as pd

from scripts.stage1_profiles import load_adjusted, load_test_bars
from tpo.data import load_config


def main(path: str) -> None:
    cfg = load_config(instrument="ES")
    rbars = load_adjusted("ES", cfg)[0]
    eth = load_test_bars("ES_ETH")[0]
    t0 = eth.ts.min()
    b = pd.concat([rbars[rbars.ts < t0], eth], ignore_index=True).sort_values("ts")
    ms = (b.ts.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)
    with open(path, "w") as f:
        for t, o, h, l, c in zip(ms, b.open, b.high, b.low, b.close):
            f.write(f"{t},{o!r},{h!r},{l!r},{c!r}\n")
    print(path, len(b), "баров")


if __name__ == "__main__":
    main(*sys.argv[1:])
