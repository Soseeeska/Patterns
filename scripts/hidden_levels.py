"""Скрытые уровни (заметки «MP FP» → Hidden Levels).

Уровень — VAH / VAL / POC полного RTH-дня D. Скрытым он становится, когда в
один из следующих hidden_lookahead_days дней (D2) через него прошли
single prints или тейл (участок строк с TPO = 1 длиной ≥ grey_min_ticks).
Такой уровень не сребалансирован и валиден, пока участок не проторгуется
(диапазон какого-нибудь дня пройдёт его целиком).
Тест — первый возврат цены к уровню после D2 по правилам этапа 6 на 30m
(удержание / пробой двумя close, отход ≥ 16 тиков, число баров теста);
берутся только возвраты до проторговки участка.
Сравнение:
  * контроль — случайная точка внутри того же участка single prints (не ближе
    hidden_control_min_dist_ticks к уровню), 20 прогонов: работает ли именно
    уровень или просто сингл-принты;
  * обычные уровни — VAH / VAL / POC дней, не прошитые сингл-принтами, первый
    возврат после дня D+1.

Запуск:  python -m scripts.hidden_levels [ES] [бары: ES | ES_ETH]
Результат: output/<TAG>_hidden_levels.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted, load_test_bars, tag
from tpo.data import ROOT, load_config
from tpo.day_context import single_runs
from tpo.intraday import run_tests


def find_hidden(daily, profiles, tick, cfg):
    cc = cfg["day_context"]
    d = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    runs = {}
    for r in d.itertuples():
        p = profiles[r.date]
        runs[r.date] = [(lo * tick, hi * tick) for lo, hi in
                        single_runs(p.counts, p.low, p.row, max(1, cc["grey_min_ticks"] // p.row))]
    H, L, dates = d.high.to_numpy(), d.low.to_numpy(), d.date.to_numpy()
    hidden, regular = [], []
    for i, r in d.iterrows():
        for lv in ("vah", "val", "poc"):
            x = getattr(r, lv)
            found = None
            for j in range(i + 1, min(i + 1 + cc["hidden_lookahead_days"], len(d))):
                for lo, hi in runs[dates[j]]:
                    if lo <= x <= hi:
                        found = (j, lo, hi)
                        break
                if found:
                    break
            if found:
                j, lo, hi = found
                if j + 1 >= len(d):
                    continue
                later = np.flatnonzero((L[j + 1:] <= lo) & (H[j + 1:] >= hi))
                filled = dates[j + 1 + later[0]] if len(later) else np.datetime64("NaT", "ns")
                hidden.append({"level": lv.upper(), "price": x, "day": r.date, "pierced_on": dates[j],
                               "run_lo": lo, "run_hi": hi, "available_from": dates[j + 1], "filled": filled})
            elif i + 1 < len(d):
                regular.append({"level": lv.upper(), "price": x, "day": r.date, "available_from": dates[i + 1]})
    return pd.DataFrame(hidden), pd.DataFrame(regular)


def first_tests(bars, pts: pd.DataFrame, cfg, tick, until=None):
    """Первый тест каждой точки (зона = точка). until — колонка с датой, после которой тест не считается."""
    bands = pd.DataFrame({"zone_id": np.arange(len(pts)), "composite_id": np.arange(len(pts)),
                          "level_type": pts.get("level", "X"), "lo": pts.price.to_numpy(),
                          "hi": pts.price.to_numpy(), "composite_days": 1,
                          "available_from": pts.available_from.to_numpy()})
    t = run_tests(bars, bands, cfg, tick)
    t = t[(t.test_no == 1) & (t.flips_before == 0)].drop_duplicates("zone_id")
    if until is not None:
        lim = pts[until].to_numpy()
        ok = [pd.isna(lim[z]) or d <= lim[z] for z, d in zip(t.zone_id, t.date)]
        t = t[ok]
    return t


def stats(t):
    return {"n": len(t), "hold": (t.outcome == "hold").mean(), "react16": (t.reaction_ticks >= 16).mean(),
            "bars": t.bars.median(), "false_break": t.false_break.mean()}


def main(instrument: str = "ES", bars_instrument: str | None = None) -> None:
    cfg = load_config(instrument=instrument)
    cc = cfg["day_context"]
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    hidden, regular = find_hidden(daily, profiles, tick, cfg)
    tb = bars
    if bars_instrument and bars_instrument != instrument:
        tb = load_test_bars(bars_instrument)[0]
        start = tb.date.min()
        hidden = hidden[hidden.available_from >= start].reset_index(drop=True)
        regular = regular[regular.available_from >= start].reset_index(drop=True)
    name = tag(instrument, bars_instrument, None)

    th = first_tests(tb, hidden, cfg, tick, until="filled")
    tr = first_tests(tb, regular, cfg, tick)
    hidden.to_csv(out / f"{name}_hidden_levels.csv", index=False)

    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    md = cc["hidden_control_min_dist_ticks"] * tick
    ctrl = []
    for _ in range(cc["placebo_runs"]):
        pts = hidden.copy()
        new = []
        for r in pts.itertuples():
            grid = np.arange(r.run_lo, r.run_hi + tick / 2, tick)
            grid = grid[np.abs(grid - r.price) >= md]
            new.append(rng.choice(grid) if len(grid) else np.nan)
        pts["price"] = new
        pts = pts[pts.price.notna()].reset_index(drop=True)
        ctrl.append(stats(first_tests(tb, pts, cfg, tick, until="filled")))

    print(f"{name}: скрытых уровней {len(hidden)} (VAH {sum(hidden.level == 'VAH')}, VAL {sum(hidden.level == 'VAL')}, "
          f"POC {sum(hidden.level == 'POC')}); проторговано позже {hidden.filled.notna().mean():.0%}; "
          f"обычных уровней {len(regular)}")
    print(f"Первых возвратов до проторговки: {len(th)}; у обычных уровней: {len(tr)}\n")
    sh, sr = stats(th), stats(tr)

    def ctl(k):
        v = np.array([c[k] for c in ctrl], float)
        lo, hi = np.percentile(v, [5, 95])
        mark = " ↑" if sh[k] > hi else (" ↓" if sh[k] < lo else "")
        return f"{v.mean():6.1%} ({lo:.0%}–{hi:.0%}){mark}"

    print(f"{'':34s} {'скрытый':>9s}  {'точка в тех же сингл-принтах':>28s}  {'обычный уровень':>16s}")
    for k, nm in (("hold", "удержание (первый возврат)"), ("react16", "отход ≥ 16 тиков"),
                  ("false_break", "ложный пробой")):
        print(f"{nm:34s} {sh[k]:9.1%}  {ctl(k):>28s}  {sr[k]:16.1%}")
    print(f"{'медиана баров в тесте':34s} {sh['bars']:9.0f}  {np.mean([c['bars'] for c in ctrl]):28.1f}  {sr['bars']:16.0f}")
    print("\nПо типу уровня (скрытый / обычный): удержание")
    for lv in ("VAH", "VAL", "POC"):
        a = th[th.level_type == lv]
        b = tr[tr.level_type == lv]
        print(f"  {lv}: {(a.outcome == 'hold').mean():.1%} (n={len(a)})   обычный {(b.outcome == 'hold').mean():.1%} (n={len(b)})")


if __name__ == "__main__":
    main(*sys.argv[1:])
