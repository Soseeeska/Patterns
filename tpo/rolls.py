"""Back-adjust непрерывного ряда без склейки.

Выгрузка переходит на новый контракт во вторник недели экспирации
(3-я пятница марта/июня/сентября/декабря минус 3 дня) — это видно по
скачку объёма в 1.5–5 раз. Спред календаря оценивается моделью carry:

    spread = close_перед_роллом * (r - q) * days_to_next_expiry / 365

Переключение на новый контракт — в понедельник накануне ролла в
roll.switch_time_ct (15:00 CT, сразу после закрытия RTH): это видно по
гэпу 50–75 п. на баре 15:00 в ETH-выгрузке. В RTH-выгрузке баров после
15:00 нет, поэтому для неё это то же самое, что «с дня ролла».

Сырой гэп в день ролла не годится: в нём ещё и ночное движение рынка
(std ~32 п., CPI-дни до ±150 п.). Ручные значения из config.roll.overrides
имеют приоритет над моделью.
"""
from __future__ import annotations

import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]


def roll_dates(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    out = []
    for y in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            first = pd.Timestamp(y, m, 1)
            third_fri = pd.date_range(first, first + pd.Timedelta(days=27), freq="W-FRI")[2]
            tue = third_fri - pd.Timedelta(days=3)
            if start < tue <= end:
                out.append(tue)
    return out


def switch_ts(roll_date: pd.Timestamp, cfg: dict) -> pd.Timestamp:
    """Момент перехода на новый контракт (tz-aware, timezone из data)."""
    t = pd.Timedelta(cfg["roll"]["switch_time_ct"] + ":00")
    return (roll_date - pd.Timedelta(days=1) + t).tz_localize(cfg["data"]["timezone"])


def roll_table(bars: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    rcfg = cfg["roll"]
    day = bars.groupby("date").agg(open=("open", "first"), close=("close", "last"),
                                   volume=("volume", "sum"))
    overrides = {pd.Timestamp(k): float(v) for k, v in (rcfg.get("overrides") or {}).items()}
    rows = []
    for d in roll_dates(day.index.min(), day.index.max()):
        if d not in day.index:
            raise ValueError(f"Нет торгового дня в дату ролла {d.date()}")
        i = day.index.get_loc(d)
        prev, cur = day.iloc[i - 1], day.iloc[i]
        sw = switch_ts(d, cfg)
        prev_close = bars.loc[bars["ts"] < sw].sort_values("ts")["close"].iloc[-1]
        q_idx = (d.month - 1) // 3
        r = rcfg["policy_rate_pct"][d.year][q_idx]
        q = rcfg["dividend_yield_pct"][d.year]
        model = prev_close * (r - q) / 100 * rcfg["days_to_next_expiry"] / 365
        spread = overrides.get(d, model)
        rows.append({
            "roll_date": d,
            "switch_ts": sw,
            "volume_ratio": cur.volume / prev.volume,
            "raw_gap": bars.loc[bars["ts"] >= sw].sort_values("ts")["open"].iloc[0] - prev_close,
            "model_spread": model,
            "spread": round(spread / tick) * tick,
            "source": "override" if d in overrides else "carry_model",
        })
    t = pd.DataFrame(rows)
    weak = t[t.volume_ratio < rcfg["min_volume_ratio"]]
    if len(weak):
        raise ValueError(f"Нет скачка объёма в дни ролла: {weak.roll_date.dt.date.tolist()}")
    return t


def back_adjust(bars: pd.DataFrame, rolls: pd.DataFrame) -> pd.DataFrame:
    """Прибавляет к ценам до каждого ролла его спред (аддитивно), чтобы
    история была в ценах текущего контракта. Сырые цены — в *_raw."""
    out = bars.copy()
    shift = pd.Series(0.0, index=out.index)
    for _, r in rolls.iterrows():
        shift[out["ts"] < r.switch_ts] += r.spread
    for c in PRICE_COLS:
        out[f"{c}_raw"] = out[c]
        out[c] = out[c] + shift
    out["roll_shift"] = shift
    return out
