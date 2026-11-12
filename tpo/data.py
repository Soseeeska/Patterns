"""Загрузка 30-минутных RTH-баров и приведение к единому виду."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | Path = ROOT / "config.yaml", instrument: str | None = None) -> dict:
    """С instrument — поверх общих параметров накладываются
    data.instruments.<instrument>.overrides (например, сессия ETH)."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if instrument:
        over = cfg["data"]["instruments"][instrument].get("overrides") or {}
        _merge(cfg, over)
    return cfg


def load_bars(instrument: str, cfg: dict) -> pd.DataFrame:
    """Возвращает бары с колонками ts (America/Chicago), date, period, open..volume.

    Формат файла: без заголовка, `YYYYMMDD HHMMSS,open,high,low,close,volume`,
    время — начало бара в часовом поясе `data.source_timezone`.
    Сессия начинается в `data.rth_start` (CT). Если сессия переходит через
    полночь (rth_start > rth_end, как у ETH 17:00–16:00), дата сессии —
    следующий календарный день (конвенция Globex).
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
    start = pd.Timedelta(dcfg["rth_start"] + ":00")
    end = pd.Timedelta(dcfg["rth_end"] + ":00")
    local = ts.dt.tz_localize(None)
    # сдвиг так, чтобы начало сессии стало началом суток её даты
    shift = (pd.Timedelta(days=1) - start) if start > end else -start
    shifted = local + shift
    df["date"] = shifted.dt.normalize()
    period_min = cfg["tpo"]["period_minutes"]
    offset = (shifted - df["date"]) / pd.Timedelta(minutes=period_min)
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
