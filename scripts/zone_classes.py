"""Деление первородных зон на поддержку / сопротивление / проторговку
по 30m-взаимодействиям (tpo/zone_classes.py) за всю жизнь зоны.

Проверки:
  * доли классов у реальных и фальш-зон (сдвиг на 1.5–3 ширины);
  * устойчивость: класс по первой половине взаимодействий зоны против
    класса по второй половине (зоны с ≥ 10 взаимодействиями).

Запуск:  python -m scripts.zone_classes [ES] [бары: ES_ETH] [с даты: 2023-07-13]
Два правила: большинство из трёх типов и «по отторжениям» (config.wicks.class_rejection_share).

Результат: output/<INSTR>_zone_classes.csv, output/<INSTR>_zone_classes_by_rejections.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, zone_frame
from scripts.stage1_profiles import align_zones, load_adjusted, load_test_bars, tag
from tpo.data import ROOT, load_config
from tpo.zone_classes import RANGE, RESISTANCE, SUPPORT, classify, interaction_types

CLASSES = [SUPPORT, RESISTANCE, RANGE]
MIN_SPLIT = 10


def classify_zones(bars: pd.DataFrame, zones: pd.DataFrame, min_wick: float, share=None) -> pd.DataFrame:
    b = bars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    first_bar = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    rows = []
    for z in zones.itertuples():
        if z.available_from not in first_bar.index:
            continue
        s = int(first_bar[z.available_from])
        t = interaction_types(O[s:], H[s:], L[s:], C[s:], z.lo, z.hi, min_wick)
        t = t[t != ""]
        half = len(t) // 2
        rows.append({
            "zone_id": z.zone_id, "lo": z.lo, "hi": z.hi, "available_from": z.available_from,
            "n_long": int((t == "long").sum()), "n_short": int((t == "short").sum()),
            "n_range": int((t == "range").sum()), "class": classify(t, share),
            "class_first_half": classify(t[:half], share) if len(t) >= MIN_SPLIT else None,
            "class_second_half": classify(t[half:], share) if len(t) >= MIN_SPLIT else None,
        })
    return pd.DataFrame(rows)


def stats(df: pd.DataFrame) -> dict:
    out = {c: (df["class"] == c).mean() for c in CLASSES}
    tot = df[["n_long", "n_short", "n_range"]].sum()
    for k in tot.index:
        out[k] = tot[k] / tot.sum()
    sp = df.dropna(subset=["class_first_half"])
    out["same_class"] = (sp.class_first_half == sp.class_second_half).mean()
    for c in CLASSES:
        s = sp[sp.class_first_half == c]
        out[f"keep_{c}"] = (s.class_second_half == c).mean() if len(s) else np.nan
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
    mw = cfg["wicks"]["min_wick_ticks"] * tick

    share = cfg["wicks"]["class_rejection_share"]
    for title, sh, suffix in (("ПРАВИЛО 1: большинство из трёх типов", None, ""),
                              (f"ПРАВИЛО 2: по отторжениям (≥ {share:.0%} в одну сторону)", share, "_by_rejections")):
        print(f"\n===== {title} =====")
        report(bars, daily, fz, mw, pc, tick, cfg, sh, out / f"{name}_zone_classes{suffix}.csv", name)


def report(bars, daily, fz, mw, pc, tick, cfg, share, path, instrument):
    real = classify_zones(bars, fz, mw, share)
    real["n_total"] = real.n_long + real.n_short + real.n_range
    real.to_csv(path, index=False, float_format="%.2f")
    rs = stats(real)
    rng = np.random.default_rng(42)
    ps = [stats(classify_zones(bars, placebo(fz, rng, pc, tick, exclusive=False), mw, share))
          for _ in range(cfg["wicks"]["placebo_runs"])]

    def line(name, key):
        p = np.array([x[key] for x in ps], float)
        p = p[~np.isnan(p)]
        if not len(p) or np.isnan(rs[key]):
            print(f"  {name:44s} {rs[key]:6.1%}   фальш —")
            return
        lo, hi = np.percentile(p, [5, 95])
        mark = " ↑" if rs[key] > hi else (" ↓" if rs[key] < lo else "")
        print(f"  {name:44s} {rs[key]:6.1%}   фальш {p.mean():6.1%} ({lo:.1%}–{hi:.1%}){mark}")

    print(f"{instrument}: {len(real)} зон, взаимодействий {real.n_total.sum()} "
          f"(медиана на зону {real.n_total.median():.0f})\n")
    print("Классы зон:")
    for c in CLASSES:
        print(f"  {c:14s} {(real['class'] == c).sum():4d} зон")
    print("\nСравнение с фальш-зонами:")
    for c in CLASSES:
        line(f"доля зон «{c}»", c)
    line("взаимодействия: лонг-отторжение", "n_long")
    line("взаимодействия: шорт-отторжение", "n_short")
    line("взаимодействия: проторговка", "n_range")
    print(f"\nУстойчивость (зоны с ≥ {MIN_SPLIT} взаимодействиями, класс 1-й половины → 2-й):")
    line("класс тот же", "same_class")
    for c in CLASSES:
        line(f"был «{c}» → остался", f"keep_{c}")

    last = daily.close.iloc[-1]
    near = real[(real.hi >= last - 150) & (real.lo <= last + 150)].sort_values("lo", ascending=False)
    print(f"\nЗоны в ±150 п. от close {last:.2f}:")
    print(near[["zone_id", "lo", "hi", "class", "n_long", "n_short", "n_range"]].to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
