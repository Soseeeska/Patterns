"""Режим дня относительно карты балансов (композитов TPO Zones 1.8) — к открытию RTH, без заглядывания вперёд.

Балансы на день D — из ZoneSnap (`comps_*.csv`): закрытые композиты, известные к открытию D, и снимок формирующегося.
Цены — в контракте дня D: цена композита + shift (роллы после него), закрытия прошлых дней — + гэпы роллов между
днём и D (`rolls.csv` движка; до 2023-07 роллы в RTH-ряду движок не видит — сдвига нет). Вариант «цены графика» — без
сдвигов, как балансы видны на несклеенном графике MotiveWave (старый баланс до ролла стоит ниже на спред).

Определения (согласованы с автором):
  * дом — баланс, в диапазоне [lo, hi] которого вчерашний close: сначала формирующийся, потом последний закрытый;
  * выход — RTH-close за диапазоном баланса; принят снаружи — два close подряд снаружи или VA дня целиком снаружи;
  * отвергли выход — после выхода close снова внутри в течение 1–3 дней.
Режимы:
  «формирующийся баланс»      — вчерашний close внутри формирующегося композита;
  «внутри старого баланса»    — внутри закрытого баланса, последний возврат в него — давно (или выхода не было);
  «отвергли выход»            — вернулись в баланс ≤ 3 дней назад после выхода на 1–3 дня (направление — куда выходили);
  «возврат в старый баланс»   — вернулись ≤ 3 дней назад после принятого выхода (> 3 дней снаружи), направление — откуда;
  «выход, не принят»          — вчера первый close за последним балансом (или выход без принятия);
  «приняты снаружи»           — снаружи всех балансов, выход принят; цель — ближайший баланс по направлению,
                                если его нет — «price discovery».
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REGIMES = ["формирующийся баланс", "внутри старого баланса", "отвергли выход", "возврат в старый баланс",
           "выход, не принят", "приняты снаружи → к балансу", "price discovery"]


def load_comps(folder: Path, chart: bool = False) -> pd.DataFrame:
    """chart=True — цены графика (как в MotiveWave, без сдвига на роллы), иначе — в контракте дня."""
    c = pd.concat([pd.read_csv(f, parse_dates=["day", "start", "end", "av"]) for f in sorted(folder.glob("comps_*.csv"))],
                  ignore_index=True).drop_duplicates(["day", "type", "start", "end"])
    if not chart:
        for k in ("lo", "hi", "val", "vah", "poc", "core_lo", "core_hi"):
            c[k] = c[k] + c["shift"]
    return c


def roll_gaps(path: Path, tz: str) -> pd.DataFrame:
    r = pd.read_csv(path)
    r["ts"] = pd.to_datetime(r.roll_ms, unit="ms", utc=True).dt.tz_convert(tz)
    return r


def adjusted_closes(days: pd.DataFrame, rolls: pd.DataFrame, upto: pd.Timestamp) -> pd.DataFrame:
    """RTH-дни до upto с ценами в контракте дня upto: + гэпы роллов после дня (ролл — в 15:00 CT, после RTH дня)."""
    d = days[days.date < upto].copy()
    end = d.date + pd.Timedelta(hours=15)
    sh = np.zeros(len(d))
    up_ts = upto.tz_localize(rolls.ts.dt.tz) + pd.Timedelta(hours=8)
    for t, g in zip(rolls.ts, rolls.gap):
        if t < up_ts:
            sh += np.where(end.dt.tz_localize(rolls.ts.dt.tz) <= t, g, 0.0)
    for k in ("close", "val", "vah"):
        d[k] = d[k] + sh
    return d


def _inside(x, b):
    return b.lo <= x <= b.hi


def regime_of_day(D: pd.Timestamp, hist: pd.DataFrame, comps: pd.DataFrame) -> dict:
    """hist — RTH-дни до D (close / val / vah в ценах D, по возрастанию даты), comps — балансы дня D."""
    y = hist.iloc[-1]
    yc = y.close
    live = comps[comps.type == "live"]
    closed = comps[comps.type == "closed"].sort_values("end")
    r = {"date": D, "regime": None, "dir": "—", "home_poc": np.nan, "home_lo": np.nan, "home_hi": np.nan,
         "home_core_lo": np.nan, "home_core_hi": np.nan, "home_val": np.nan, "home_vah": np.nan,
         "home_primary": np.nan, "tgt_lo": np.nan, "tgt_hi": np.nan, "tgt_core_lo": np.nan, "tgt_core_hi": np.nan,
         "days_out": 0}

    def put_home(b):
        r.update(home_poc=b.poc, home_lo=b.lo, home_hi=b.hi, home_core_lo=b.core_lo, home_core_hi=b.core_hi,
                 home_val=b.val, home_vah=b.vah, home_primary=b.primary)

    if len(live) and _inside(yc, live.iloc[-1]):
        put_home(live.iloc[-1])
        r["regime"] = "формирующийся баланс"
        return r
    home = closed[(closed.lo <= yc) & (closed.hi >= yc)]
    if len(home):
        b = home.iloc[-1]
        put_home(b)
        after = hist[hist.date > b.end]
        out = ((after.close > b.hi) | (after.close < b.lo)).to_numpy()
        side = np.where(after.close > b.hi, "вверх", "вниз")
        r["regime"] = "внутри старого баланса"
        if out.any():
            # последняя серия закрытий снаружи и сколько дней назад вернулись
            j = len(out) - 1
            while j >= 0 and not out[j]:
                j -= 1
            back_ago = len(out) - 1 - j          # дней внутри после серии (≥ 1, т.к. вчера внутри)
            k = j
            while k >= 0 and out[k]:
                k -= 1
            run = j - k
            if back_ago <= 3:
                r["dir"] = side[j]
                r["days_out"] = int(run)
                r["regime"] = "отвергли выход" if run <= 3 else "возврат в старый баланс"
        return r
    # снаружи всех балансов: от последнего (по времени) баланса
    allb = pd.concat([closed, live]).sort_values("end")
    if allb.empty:
        r["regime"] = "price discovery"
        return r
    b = allb.iloc[-1]
    put_home(b)
    up = yc > b.hi
    r["dir"] = "вверх" if up else "вниз"
    tail = hist.iloc[-2:]
    outside2 = bool(((tail.close > b.hi) if up else (tail.close < b.lo)).all()) and len(tail) == 2
    va_out = (y.val > b.hi) if up else (y.vah < b.lo)
    if not (outside2 or va_out):
        r["regime"] = "выход, не принят"
        return r
    # цель — ближайший баланс по направлению (по ядру), за ценой
    cand = allb[allb.lo > yc] if up else allb[allb.hi < yc]
    if len(cand):
        t = cand.loc[(cand.lo - yc).idxmin()] if up else cand.loc[(yc - cand.hi).idxmin()]
        r.update(tgt_lo=t.lo, tgt_hi=t.hi, tgt_core_lo=t.core_lo, tgt_core_hi=t.core_hi)
        r["regime"] = "приняты снаружи → к балансу"
    else:
        r["regime"] = "price discovery"
    return r


def day_outcomes(r: dict, day, g: pd.DataFrame) -> dict:
    """Исходы дня D по его RTH-барам (цены D) для режима r; day — строка day_table."""
    H, L = g.high.max(), g.low.min()
    o, c = day.open, day.close
    up = r["dir"] == "вверх"
    out = {
        "close_up": float(c > o),
        "y_poc": float(L <= day.y_poc <= H),
        "y_high": float(H > day.y_high),
        "y_low": float(L < day.y_low),
        "close_pos": (c - L) / (H - L) if H > L else 0.5,
    }
    if not np.isnan(r["home_core_lo"]):
        out["home_core"] = float(L <= r["home_core_hi"] and H >= r["home_core_lo"])
        out["home_poc"] = float(L <= r["home_poc"] <= H)
    if r["regime"] in ("отвергли выход",):
        # дальний край VA баланса — противоположный выходу
        far = r["home_val"] if up else r["home_vah"]
        out["far_va"] = float(L <= far) if up else float(H >= far)
        out["dir_close"] = float(c < o) if up else float(c > o)          # день против выхода
    if r["regime"] in ("выход, не принят", "приняты снаружи → к балансу", "price discovery"):
        edge = r["home_hi"] if up else r["home_lo"]
        out["back_in"] = float(L <= edge) if up else float(H >= edge)      # вернулись в диапазон баланса
        out["dir_close"] = float(c > o) if up else float(c < o)           # день по направлению выхода
        out["ext"] = float(H > day.y_high) if up else float(L < day.y_low)  # продолжение за вчерашний экстремум
        if not np.isnan(r["tgt_lo"]):
            near = r["tgt_lo"] if up else r["tgt_hi"]
            out["tgt_edge"] = float(H >= near) if up else float(L <= near)
    if r["regime"] == "возврат в старый баланс":
        out["dir_close"] = float(c < o) if up else float(c > o)           # вернулись сверху — день вниз
    return out
