"""Серые зоны: сколько их, какие они и правда ли заполняют пустоты между нормальными.

Серые зоны — tpo/grey_zones.py (короткие композиты, слабые HVN-дни, молодые
первородные зоны), нормальные — старые балансы и первородные HVN-зоны с историей.
Для каждой серой зоны на день её появления (для молодой — тоже день появления,
когда она ещё серая) смотрим нормальные зоны в пределах ±near_points и
определяем положение: в пустоте / с краю / частично / внутри.
Фон — те же серые зоны, сдвинутые на U(1.5, 3) своей ширины (не меньше 4 п.)
вверх или вниз; нормальные зоны не двигаются. Гипотеза «серые заполняют пустоты»
подтверждается, если «в пустоте» у реальных выше, а покрытие нормальными — ниже,
чем у сдвинутых (за пределами 5–95% фальш-прогонов).
Ещё — покрытие цен ±30 п. от open каждого дня: нормальными, серыми, вместе.

Запуск:  python -m scripts.grey_zones [ES]
Результат: output/<INSTR>_grey_zones.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.day_context import active, build_zone_map
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.grey_zones import POSITIONS, classify_vs_normal

SRC_NAME = {"comp": "короткий композит", "hvn": "слабый HVN-день", "young": "молодая первородная"}


def grey_at_birth(zmap: pd.DataFrame) -> pd.DataFrame:
    """Серые зоны на день появления: lo, hi, date, src."""
    g = zmap[zmap.type == "grey"][["lo", "hi", "available_from", "src"]]
    y = zmap[zmap.young & (zmap.grey_until.isna() | (zmap.grey_until > zmap.available_from))]
    y = y[["lo", "hi", "available_from"]].assign(src="young")
    return pd.concat([g, y], ignore_index=True).rename(columns={"available_from": "date"})


def positions(grey: pd.DataFrame, normal_on: dict, near: float, tick: float) -> pd.DataFrame:
    out = []
    for r in grey.itertuples():
        n = normal_on[r.date]
        n = n[(n.lo <= r.hi + near) & (n.hi >= r.lo - near)]
        pos, cov = classify_vs_normal(r.lo, r.hi, n.lo.to_numpy(), n.hi.to_numpy(), tick)
        out.append((pos, cov))
    return pd.DataFrame(out, columns=["pos", "covered"], index=grey.index)


def shifted(grey: pd.DataFrame, rng, tick: float) -> pd.DataFrame:
    w = np.maximum(grey.hi - grey.lo, 4.0)
    sh = np.round(rng.uniform(1.5, 3.0, len(grey)) * w * rng.choice([-1, 1], len(grey)) / tick) * tick
    return grey.assign(lo=grey.lo + sh, hi=grey.hi + sh)


def summary(p: pd.DataFrame) -> dict:
    d = {k: (p.pos == k).mean() for k in POSITIONS}
    d["covered"] = p.covered.mean()
    return d


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    gc = cfg["grey_zones"]
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    zmap, prim, _ = build_zone_map(cfg, bars, daily, profiles, tick)
    grey = grey_at_birth(zmap)
    near = gc["near_points"]

    print(f"{instrument}: нормальных — старых балансов {(zmap.type == 'old_balance').sum()}, "
          f"первородных {len(prim)} (из них серые на старте {zmap.young.sum()}: формирование + подтверждения "
          f"< {gc['min_history_days']} дн.)")
    print(f"Серые зоны по источникам (ширина {gc['min_width_points']:g}–{gc['max_width_points']:g} п.):")
    for s, g in grey.groupby("src"):
        w = g.hi - g.lo
        print(f"  {SRC_NAME[s]:22s} {len(g):4d}   ширина: медиана {w.median():.2f} п., "
              f"25–75% {w.quantile(.25):.2f}–{w.quantile(.75):.2f}, уже 2 п. {(w < 2).mean():.0%}")
    yz = zmap[zmap.young]
    became = yz.grey_until.notna().mean()
    print(f"  молодые первородные: позже набрали историю и стали нормальными — {became:.0%}")

    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    plac_grey = [shifted(grey, rng, tick) for _ in range(gc["placebo_runs"])]
    act = {d: active(zmap, d) for d in grey.date.unique()}
    for title, keep in (("нормальные = старые балансы + HVN", ("old_balance", "hvn")),
                        ("нормальные = только HVN (без широких VA старых балансов)", ("hvn",))):
        normal_on = {d: a[a.type.isin(keep)] for d, a in act.items()}
        real = positions(grey, normal_on, near, tick)
        plac = [positions(pg, normal_on, near, tick) for pg in plac_grey]
        if keep == ("old_balance", "hvn"):
            grey = grey.join(real)
        print(f"\n===== {title} =====")
        print(f"Положение серой зоны среди нормальных (±{near:g} п.) на день появления; "
              f"[сдвинутые: среднее (5–95%)], ↑/↓ — за пределами фона")
        for name, m in [("все", np.ones(len(grey), bool))] + [(SRC_NAME[s], (grey.src == s).to_numpy()) for s in SRC_NAME]:
            r = summary(real[m])
            ps = [summary(p[m]) for p in plac]
            print(f"\n  {name} (n={m.sum()}):")
            for k in POSITIONS + ["covered"]:
                v = np.array([p[k] for p in ps])
                lo, hi = np.percentile(v, [5, 95])
                mark = " ↑" if r[k] > hi else (" ↓" if r[k] < lo else "")
                label = "покрыто нормальными (доля цен зоны)" if k == "covered" else k
                print(f"    {label:36s} {r[k]:6.1%}   [{v.mean():5.1%} ({lo:.1%}–{hi:.1%})]{mark}")

    # покрытие цен вокруг open
    days = daily[~daily.short_day].sort_values("date")
    days = days[days.date >= zmap.available_from.min()]
    cov = []
    for t in days.itertuples():
        a = active(zmap, t.date)
        grid = np.arange(round((t.open - 30) / tick), round((t.open + 30) / tick) + 1) * tick
        mn, mg = np.zeros(len(grid), bool), np.zeros(len(grid), bool)
        for z in a[(a.hi >= grid[0]) & (a.lo <= grid[-1])].itertuples():
            m = (grid >= z.lo) & (grid <= z.hi)
            if z.type == "grey":
                mg |= m
            else:
                mn |= m
        cov.append((mn.mean(), mg.mean(), (mn | mg).mean(), (mg & ~mn).mean()))
    c = np.array(cov).mean(axis=0)
    print(f"\nПокрытие цен ±30 п. от open ({len(days)} дней): нормальные {c[0]:.0%}, серые {c[1]:.0%}, "
          f"вместе {c[2]:.0%}; серые добавляют {c[3]:.0%} цен, которых нет в нормальных")

    grey.to_csv(out / f"{instrument}_grey_zones.csv", index=False, float_format="%.3f")
    print(out / f"{instrument}_grey_zones.csv")


if __name__ == "__main__":
    main(*sys.argv[1:])
