"""Хвосты 30m-свечей у первородных HVN-зон: валидация зон по хвостам.

Зоны — первородные (уход цены по HVN, все зачётные дни), берутся со дня
available_from. Определения хвостов — tpo/wicks.py.

A. Бары, задевшие зону: как часто основной / противоположный хвост
   пересекает границу или лежит внутри, как часто close внутри зоны.
B. Тесты зоны по правилу этапа 6 (два close за зоной — пробой, бар
   целиком на исходной стороне — удержание). Срез по первому бару теста:
   хвост в сторону зоны пересёк границу, а close остался на стороне подхода
   (отторжение), — основной это хвост или противоположный.
C. Срез тестов по числу хвостов через границу, накопленных зоной до теста.
D. Самые «живые» зоны: больше всего хвостов через границу и close внутри.
Всё сравнивается с фальш-зонами (сдвиг на 1.5–3 ширины).

Запуск:  python -m scripts.zone_wicks [ES] [бары: ES_ETH] [с даты: 2023-07-13]
         (зоны всегда по профилям первого инструмента; второй — бары для тестов)
Результат: output/<INSTR>_zone_wicks_tests.csv, output/<INSTR>_zone_wicks_zones.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, zone_frame
from scripts.primary_zones_variants import bands_for_30m
from scripts.stage1_profiles import align_zones, load_adjusted, load_test_bars, tag
from tpo.data import ROOT, load_config
from tpo.intraday import run_tests
from tpo.wicks import BOUNDARY, INSIDE, main_opposite, wick_positions


def analyze(bars: pd.DataFrame, zones: pd.DataFrame, cfg: dict, tick: float):
    """Возвращает (бары-касания со статусами хвостов, тесты с признаками, сводку по зонам)."""
    b = bars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    first_bar = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    key = {(d, p): i for i, (d, p) in enumerate(zip(b.date, b.period))}
    mw = cfg["wicks"]["min_wick_ticks"] * tick

    touch_rows, zone_rows, cum = [], [], {}
    for z in zones.itertuples():
        if z.available_from not in first_bar.index:
            continue
        s = int(first_bar[z.available_from])
        o, h, l, c = O[s:], H[s:], L[s:], C[s:]
        lw, uw = wick_positions(o, h, l, c, z.lo, z.hi, mw)
        main, opp, _ = main_opposite(o, c, lw, uw)
        touch = (l <= z.hi) & (h >= z.lo)
        bnd = (lw == BOUNDARY) | (uw == BOUNDARY)
        cum[z.zone_id] = (s, np.cumsum(bnd), lw, uw, main)
        t = touch
        touch_rows.append({
            "bars": int(t.sum()),
            "main_boundary": int((t & (main == BOUNDARY)).sum()), "main_inside": int((t & (main == INSIDE)).sum()),
            "opp_boundary": int((t & (opp == BOUNDARY)).sum()), "opp_inside": int((t & (opp == INSIDE)).sum()),
            "close_inside": int((t & (c >= z.lo) & (c <= z.hi)).sum()),
        })
        zone_rows.append({"zone_id": z.zone_id, "lo": z.lo, "hi": z.hi, "available_from": z.available_from,
                          **touch_rows[-1], "boundary_wicks": int(bnd.sum())})
    tch = pd.DataFrame(touch_rows).sum()

    tests = run_tests(bars, bands_for_30m(zones), cfg, tick)
    feats = []
    for t in tests.itertuples():
        i = key[(t.date, t.period)]
        s, cb, lw, uw, main = cum[t.zone_id]
        k = i - s
        sup = t.role == "support"
        pos = lw[k] if sup else uw[k]
        z = zones.loc[zones.zone_id == t.zone_id].iloc[0]
        side_ok = C[i] > z.hi if sup else C[i] < z.lo
        rej = (pos == BOUNDARY) and side_ok
        is_main = pos == main[k]
        feats.append({"reject_first": rej,
                      "reject_kind": ("основной" if is_main else "противоположный") if rej else "нет",
                      "prior_boundary": int(cb[k - 1]) if k > 0 else 0})
    tests = pd.concat([tests.reset_index(drop=True), pd.DataFrame(feats)], axis=1)
    return tch, tests, pd.DataFrame(zone_rows)


def bucket(n: int) -> str:
    return "0" if n == 0 else "1–5" if n <= 5 else "6–20" if n <= 20 else "21+"


def summary(tch: pd.Series, t: pd.DataFrame) -> dict:
    n = tch["bars"]
    out = {f"A: {k}": tch[k] / n for k in ("main_boundary", "main_inside", "opp_boundary", "opp_inside", "close_inside")}
    out["A: баров-касаний"] = n
    t = t.assign(pb=t.prior_boundary.map(bucket))
    cuts = {"все тесты": t,
            "1-й бар: отторжение хвостом": t[t.reject_first],
            "  основной хвост": t[t.reject_kind == "основной"],
            "  противоположный хвост": t[t.reject_kind == "противоположный"],
            "1-й бар: без отторжения": t[~t.reject_first]}
    for role, name in (("support", "поддержка"), ("resistance", "сопротивление")):
        cuts[f"отторжение, {name}"] = t[t.reject_first & (t.role == role)]
    for bk in ("0", "1–5", "6–20", "21+"):
        cuts[f"хвостов через границу до теста: {bk}"] = t[t.pb == bk]
    for name, s in cuts.items():
        out[f"B: {name} | n"] = len(s)
        out[f"B: {name} | удерж."] = (s.outcome == "hold").mean() if len(s) else np.nan
        out[f"B: {name} | отход≥16т"] = (s.reaction_ticks >= 16).mean() if len(s) else np.nan
    return out


def main(instrument: str = "ES", bars_instrument: str | None = None, since: str | None = None) -> None:
    cfg = load_config(instrument=instrument)
    zc, pc = cfg["hvn_zones"], cfg["primary_zones"]
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["ok"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    z = zone_frame(days, daily, cfg, tick)
    fz = z[z.available_from.notna()].reset_index(drop=True)
    if bars_instrument or since:          # зоны — по профилям instrument, бары для тестов — другие
        bars, _ = load_test_bars(bars_instrument or instrument, since)
        fz = align_zones(fz, bars).reset_index(drop=True)
    name = tag(instrument, bars_instrument, since)

    tch, tests, zsum = analyze(bars, fz, cfg, tick)
    tests.to_csv(out / f"{name}_zone_wicks_tests.csv", index=False)
    zsum.to_csv(out / f"{name}_zone_wicks_zones.csv", index=False, float_format="%.2f")
    real = summary(tch, tests)

    rng = np.random.default_rng(42)
    plac = []
    for _ in range(cfg["wicks"]["placebo_runs"]):
        pz = placebo(fz, rng, pc, tick, exclusive=False).reset_index(drop=True)
        ptch, ptests, _ = analyze(bars, pz, cfg, tick)
        plac.append(summary(ptch, ptests))

    print(f"{name}: зон {len(fz)}, баров {len(bars)} ({bars.date.min():%Y-%m-%d} – {bars.date.max():%Y-%m-%d}), тестов {len(tests)}; хвост ≥ {cfg['wicks']['min_wick_ticks']} тиков\n")
    print(f"{'метрика':62s} {'реальные':>9s} {'фальш':>9s}  5–95% фальш")
    for k, v in real.items():
        if k.endswith("| n") or k == "A: баров-касаний":
            pv = np.mean([p[k] for p in plac])
            print(f"{k:62s} {v:9.0f} {pv:9.0f}")
            continue
        ps = np.array([p[k] for p in plac], float)
        ps = ps[~np.isnan(ps)]
        lo, hi = np.percentile(ps, [5, 95])
        mark = " ↑" if v > hi else (" ↓" if v < lo else "")
        print(f"{k:62s} {v:9.1%} {ps.mean():9.1%}  {lo:.1%}–{hi:.1%}{mark}")

    zsum["activity"] = zsum.boundary_wicks + zsum.close_inside
    zsum["bnd_per_touch"] = zsum.boundary_wicks / zsum.bars.replace(0, np.nan)
    top = zsum.sort_values("activity", ascending=False).head(10)
    print("\nСамые «живые» зоны (хвосты через границу + close внутри):")
    print(top[["zone_id", "lo", "hi", "available_from", "bars", "boundary_wicks", "close_inside"]]
          .to_string(index=False, formatters={"available_from": "{:%Y-%m-%d}".format}))
    t = tests.merge(zsum[["zone_id", "activity"]], on="zone_id")
    q = t.groupby(pd.qcut(t.activity.rank(method="first"), 4, labels=["Q1 тихие", "Q2", "Q3", "Q4 живые"]),
                  observed=True).outcome.agg(["size", lambda s: (s == "hold").mean()])
    q.columns = ["тестов", "удерж."]
    print("\nУдержание по квартилям активности зоны за всю жизнь (с заглядыванием вперёд, только описание):")
    print(q.round(3).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
