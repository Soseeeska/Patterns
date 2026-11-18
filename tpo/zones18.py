"""Зоны TPO Zones 1.8 (Java-движок) × классический рыночный профиль внутри дня.

Снимки зон — `motivewave/parity/ZoneSnap.java`: на каждый RTH-день зоны так, как их видит индикатор к открытию RTH
(данные строго до 08:30 CT этого дня, история 1500 календарных дней). Цены — графика (ESZ6 без склейки), как в
MotiveWave; бары тестов — сырые RTH 30m того же ряда.

Событие — первый за RTH-день тест зоны (см. `first_tests`):
  * сторона — по close предыдущего бара (для периода A — по open дня): выше зоны → зона поддержка, ниже → сопротивление;
    цена внутри зоны — тест не начинается;
  * касание — бар дотянулся до зоны (± tol);
  * прокол — насколько цена зашла за дальний край зоны за время теста (до решения), п.;
  * удержание / пробой — как в этапе 6 (`tpo/intraday.py`): пробой — два close подряд за дальним краем, удержание —
    раньше пробоя бар целиком на исходной стороне; до конца RTH не решилось — `none`;
  * сделка от зоны: вход по ближнему краю на баре касания, стоп за дальним краем + stop_pts, цель 2R
    (R = ширина + stop_pts). На баре касания считается только стоп (порядок high / low внутри бара неизвестен), дальше
    стоп и цель в одном баре — стоп. Не решилось к концу RTH — по close последнего бара, в R.
Контекст на момент касания — только по барам дня до бара касания, open и вчерашнему RTH-профилю.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tpo.profile import daily_profiles
from tpo.rolls import roll_dates

PERIOD_GROUP = {0: "A", 1: "B"} | {k: "C–F" for k in range(2, 6)} | {k: "G–M" for k in range(6, 13)}


def load_snaps(folder: Path) -> pd.DataFrame:
    parts = [pd.read_csv(f, parse_dates=["day"]) for f in sorted(folder.glob("snap_*.csv"))]
    z = pd.concat(parts, ignore_index=True).drop_duplicates(["day", "lo", "hi"])
    z["role_color"] = z.kind.map({"CORE": "синяя", "HVN_S": "зелёная", "HVN_R": "красная", "GREY": "серая"})
    return z


def day_table(bars: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    """Дневные RTH-профили (сырые цены) + вчерашние уровни; день ролла (первый RTH нового контракта) помечен."""
    d, _ = daily_profiles(bars, cfg, tick)
    d = d.sort_values("date").reset_index(drop=True)
    for c in ("high", "low", "close", "poc", "vah", "val", "short_day", "ib_high", "ib_low"):
        d["y_" + c] = d[c].shift(1)
    d["ib_w"] = d.ib_high - d.ib_low
    d["ib_avg20"] = d.ib_w.where(~d.short_day).rolling(20, min_periods=10).mean().shift(1)
    rolls = set(roll_dates(d.date.min(), d.date.max()))
    d["roll_day"] = d.date.isin(rolls)
    return d


def open_loc(o, y) -> str:
    if o > y.y_high:
        return "выше High"
    if o > y.y_vah:
        return "VAH–High"
    if o >= y.y_val:
        return "внутри VA"
    if o >= y.y_low:
        return "Low–VAL"
    return "ниже Low"


def first_tests(day, g: pd.DataFrame, zones: pd.DataFrame, tick: float, tol_ticks: int = 2,
                stop_pts: float = 2.0, rr: float = 2.0) -> list[dict]:
    """Первые тесты зон за RTH-день. day — строка day_table, g — 13 баров дня, zones — lo / hi (+ любые колонки)."""
    O0 = g.open.iloc[0]
    H, L, C = g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy()
    n = len(g)
    tol = tol_ticks * tick
    ref = np.concatenate([[O0], C[:-1]])          # сторона: open для A, close предыдущего бара дальше
    lo_all, hi_all = zones.lo.to_numpy(), zones.hi.to_numpy()
    # зоны, до которых цена дня вообще дотягивалась
    cand = np.flatnonzero((lo_all - tol <= H.max()) & (hi_all + tol >= L.min()))
    out = []
    for zi in cand:
        lo, hi = lo_all[zi], hi_all[zi]
        t = None
        for k in range(n):
            if ref[k] > hi or ref[k] < lo:
                if L[k] <= hi + tol and H[k] >= lo - tol:
                    t = k
                    break
        if t is None:
            continue
        above = ref[t] > hi
        # удержание / пробой
        outcome, end = "none", n - 1
        fb = False
        for j in range(t, n):
            beyond = C[j] < lo if above else C[j] > hi
            if beyond:
                if j + 1 < n and (C[j + 1] < lo if above else C[j + 1] > hi):
                    outcome, end = "break", j + 1
                    break
                fb = True
            away = L[j] > hi + tol if above else H[j] < lo - tol
            if away and j > t:
                outcome, end = "hold", j
                break
        # прокол: насколько цена зашла за дальний край за время теста (до решения), п.
        seg_t = slice(t, end + 1)
        pierce = max(0.0, (lo - L[seg_t].min()) if above else (H[seg_t].max() - hi))
        # сделка от зоны
        risk = (hi - lo) + stop_pts
        entry = hi if above else lo
        stop = lo - stop_pts if above else hi + stop_pts
        target = entry + rr * risk if above else entry - rr * risk
        res_r = None
        for j in range(t, n):
            hit_stop = L[j] <= stop if above else H[j] >= stop
            if hit_stop:
                res_r = -1.0
                break
            if j > t and (H[j] >= target if above else L[j] <= target):
                res_r = rr
                break
        if res_r is None:
            res_r = ((C[-1] - entry) if above else (entry - C[-1])) / risk
            win = np.nan
        else:
            win = float(res_r > 0)
        seg = slice(t + 1, min(t + 4, n - 1) + 1)
        mfe = ((H[seg].max() - hi) if above else (lo - L[seg].min())) if t + 1 < n else 0.0
        # контекст до бара касания
        if t >= 1:
            dh, dl = H[:t].max(), L[:t].min()
        else:
            dh = dl = O0
        nh = sum(H[j] > H[:j].max() for j in range(1, t))
        nl = sum(L[j] < L[:j].min() for j in range(1, t))
        ibh, ibl = H[:2].max(), L[:2].min()
        re_up = t >= 3 and H[2:t].max() > ibh
        re_dn = t >= 3 and L[2:t].min() < ibl
        a_close = C[0]
        r = {"date": day.date, "zi": zi, "lo": lo, "hi": hi, "width": hi - lo, "t": t,
             "role": "поддержка" if above else "сопротивление", "outcome": outcome, "false_break": fb,
             "r": res_r, "win": win, "mfe": max(mfe, 0.0), "pierce": pierce,
             "period": PERIOD_GROUP[t],
             "open_loc": open_loc(O0, day),
             "a_accept": "—" if t == 0 else ("принят" if day.y_val <= a_close <= day.y_vah else "отвергнут"),
             "control": int(np.sign(nh - nl)),
             "re": "—" if t < 3 else ("обе" if re_up and re_dn else "вверх" if re_up else "вниз" if re_dn else "нет"),
             # зона за экстремумом дня в сторону теста (тест на новом High / Low) или внутри диапазона (откат)
             "range_pos": ("за экстремумом" if (above and hi < dl) or (not above and lo > dh) else "внутри диапазона")
             if t >= 1 else "A",
             "zone_vs_yva": "над VA" if lo > day.y_vah else ("под VA" if hi < day.y_val else "в VA"),
             "ib_rel": "—" if t < 2 or not day.ib_avg20 > 0 else
             ("узкий" if (ibh - ibl) / day.ib_avg20 < 0.75 else "широкий" if (ibh - ibl) / day.ib_avg20 > 1.25 else "средний"),
             }
        refs = {"VAH": day.y_vah, "VAL": day.y_val, "POC": day.y_poc, "High": day.y_high, "Low": day.y_low}
        hits = [k for k, v in refs.items() if lo - 1.0 <= v <= hi + 1.0]
        r["confl"] = "+".join(hits) if hits else "нет"
        out.append(r)
    return out
