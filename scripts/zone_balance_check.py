"""Подтверждённые зоны как балансы: проторговывается ли в них цена чаще.

День × первородная HVN-зона, которую диапазон дня задел. Исход — POC дня внутри
зоны (± допуск), т.е. цена проторговалась в зоне. Признаки на день (только
история до него):
  conf_0 / conf_2 — сколько зачётных HVN-дней (8–10 TPO) после появления зоны
                    легли полосой в зону (0) или рядом с ней (≤ 2 п.);
  purity          — доля отторжений хвостом в одну сторону (≥ 3 отторжений).
Сравнение со сдвинутыми зонами (1.5–3 ширины, 5 прогонов) с пересчитанными
подтверждениями и отторжениями. Таблицы — до 2025 и после.

Запуск:  python -m scripts.zone_balance_check [ES]
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.day_context import hvn_class_table
from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config


def pairs(daily, zones, cls_tab, q, tick, tol):
    d = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    qd = q.date.to_numpy(dtype="datetime64[ns]")
    qlo, qhi = q.band_lo.to_numpy(), q.band_hi.to_numpy()
    rows = []
    for z in zones.itertuples():
        after = qd >= np.datetime64(z.available_from)
        gap = np.maximum.reduce([np.zeros(after.sum()), qlo[after] - z.hi, z.lo - qhi[after]])
        c0 = np.sort(qd[after][gap <= 0])
        c2 = np.sort(qd[after][gap <= 2.0])
        lg, sh = cls_tab[z.zone_id]
        m = (d.date >= z.available_from) & (d.low <= z.hi) & (d.high >= z.lo)
        for r in d[m].itertuples():
            dn = np.datetime64(r.date)
            nl, ns = int(np.searchsorted(lg, dn)), int(np.searchsorted(sh, dn))
            rows.append({"date": r.date, "zone_id": z.zone_id,
                         "conf_0": int(np.searchsorted(c0, dn)), "conf_2": int(np.searchsorted(c2, dn)),
                         "purity": max(nl, ns) / (nl + ns) if nl + ns >= 3 else np.nan,
                         "poc_in": bool(z.lo - tol <= r.poc <= z.hi + tol)})
    return pd.DataFrame(rows)


def buckets(p):
    out = {}
    for col in ("conf_0", "conf_2"):
        for nm, m in (("0", p[col] == 0), ("1", p[col] == 1), ("2–3", p[col].between(2, 3)), ("4+", p[col] >= 4)):
            out[f"{col}:{nm}"] = (p.poc_in[m].mean(), int(m.sum()))
    for nm, m in (("< 0.6 (обе стороны)", p.purity < 0.6), ("≥ 0.6 (одна сторона)", p.purity >= 0.6),
                  ("нет 3 отторжений", p.purity.isna())):
        out[f"purity:{nm}"] = (p.poc_in[m].mean(), int(m.sum()))
    m = (p.conf_2 >= 2) & (p.purity < 0.6)
    out["conf_2 ≥ 2 и чистота < 0.6"] = (p.poc_in[m].mean(), int(m.sum()))
    return out


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    zc, pc = cfg["hvn_zones"], cfg["primary_zones"]
    tol = cfg["day_context"]["zone_tolerance_ticks"] * 0.25
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    daily = daily.merge(dd[["date"]], on="date", how="left")
    z = zone_frame(dd, daily, cfg, tick)
    fz = z[z.available_from.notna()].reset_index(drop=True)
    q = dd[dd.ok]
    share = cfg["wicks"]["class_rejection_share"]
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    split = pd.Timestamp(cfg["day_context"]["split_date"])

    real = pairs(daily, fz, hvn_class_table(bars, fz, mw, share), q, tick, tol)
    rb = buckets(real)
    rpre, rpost = buckets(real[real.date < split]), buckets(real[real.date >= split])
    rng = np.random.default_rng(42)
    pl = []
    for _ in range(5):
        pz = placebo(fz, rng, pc, tick, exclusive=False).reset_index(drop=True)
        pl.append(buckets(pairs(daily, pz, hvn_class_table(bars, pz, mw, share), q, tick, tol)))

    print(f"{instrument}: пар «день × задетая зона» {len(real)}; POC дня в зоне в среднем {real.poc_in.mean():.1%}\n")
    print(f"{'срез':34s} {'реальные':>9s} {'n':>6s}  {'сдвинутые (5)':>20s}   до / после 2025")
    for k, (v, n) in rb.items():
        pv = [p[k][0] for p in pl if not np.isnan(p[k][0])]
        rng_s = f"{np.mean(pv):5.1%} ({min(pv):.0%}–{max(pv):.0%})" if pv else "—"
        print(f"{k:34s} {v:9.1%} {n:6d}  {rng_s:>20s}   {rpre[k][0]:.1%} / {rpost[k][0]:.1%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
