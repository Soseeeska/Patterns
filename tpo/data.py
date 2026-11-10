"""Загрузка 30-минутных RTH-баров и приведение к единому виду."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_bars(instrument: str, cfg: dict) -> pd.DataFrame:
    """Возвращает бары с колонками ts (America/Chicago), date, period, open..volume.

    Формат файла: без заголовка, `YYYYMMDD HHMMSS,open,high,low,close,volume`,
    время — начало бара в часовом поясе `data.source_timezone`.
    """
    dcfg = cfg["data"]
    icfg = dcfg["instruments"][instrument]
    df = pd.read_csv(
        ROOT / icfg["file"],
        header=None,
        names=["ts", "open", "high", "low", "close", "volume"],
    )
    ts = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S")
    ts = ts.dt.tz_localize(dcfg["source_timezone"]).dt.tz_convert(dcfg["timezone"])
    df["ts"] = ts
    df["date"] = ts.dt.tz_localize(None).dt.normalize()

    start = pd.Timedelta(dcfg["rth_start"] + ":00")
    tod = ts.dt.tz_localize(None) - df["date"]
    period_min = cfg["tpo"]["period_minutes"]
    offset = (tod - start) / pd.Timedelta(minutes=period_min)
    bad = (offset % 1 != 0) | (offset < 0)
    if bad.any():
        raise ValueError(
            f"{int(bad.sum())} баров не выровнены по началу RTH "
            f"({dcfg['rth_start']}), первый: {df.loc[bad, 'ts'].iloc[0]}"
        )
    df["period"] = offset.astype(int)
    if (df["period"] >= dcfg["expected_periods_per_day"]).any():
        raise ValueError("Есть бары после конца RTH")
    if df.duplicated(["date", "period"]).any():
        raise ValueError("Дубли баров")
    return df.sort_values("ts").reset_index(drop=True)
