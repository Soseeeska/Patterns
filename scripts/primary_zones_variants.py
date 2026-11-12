"""Первородные зоны: сравнение вариантов.

  * уход цены: по HVN-полосе (как в primary_zones) или по VA дня;
  * дни: все зачётные (HVN 8–10) или только «чистые» — POC в средней
    половине VA и HVN-полоса набрана поздними периодами (J–M) не больше чем
    наполовину (config.primary_zones.clean_day);
  * тесты: дневные (как в primary_zones) и 30m с флипом (как в этапе 6:
    два close подряд за зоной — пробой, бар целиком на исходной стороне —
    удержание).
Для каждого варианта — фальш-зоны со сдвигом на 1.5–3 ширины.

Запуск:  python -m scripts.primary_zones_variants [ES]
Результат: output/<INSTR>_primary_variants.csv,
           output/<INSTR>_primary_zones_<вариант>.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, raw_confirmations, zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.intraday import run_tests
from tpo.primary_zones import zone_tests


def late_share(bars: pd.DataFrame, days: pd.DataFrame, tick: float, late_from: int) -> pd.Series:
    """Доля TPO в HVN-полосе дня, поставленных периодами ≥ late_from."""
    b = bars.merge(days[["date", "band_lo", "band_hi"]], on="date")
    rows = (np.minimum(b.high, b.band_hi) - np.maximum(b.low, b.band_lo)) / tick + 1
    b["tpo"] = rows.clip(lower=0)
    tot = b.groupby("date").tpo.sum()
    late = b[b.period >= late_from].groupby("date").tpo.sum().reindex(tot.index, fill_value=0)
    return (late / tot).rename("late_share")


def bands_for_30m(z: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"zone_id": z.zone_id, "composite_id": z.zone_id, "level_type": "HVN1",
                         "lo": z.lo, "hi": z.hi, "composite_days": z.get("formation_days", 1),
                         "available_from": z.available_from})


def m30(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0, "hold": np.nan, "react16": np.nan}
    return {"n": len(t), "hold": (t.outcome == "hold").mean(), "react16": (t.reaction_ticks >= 16).mean()}


def fmt(v, p):
    p = np.array([x for x in p if not np.isnan(x)])
    if not len(p):
        return f"{v:.1%}"
    lo, hi = np.percentile(p, [5, 95])
    mark = "↑" if v > hi else ("↓" if v < lo else "")
    return f"{v:.1%} [{p.mean():.1%}]{mark}"


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    zc, pc = cfg["hvn_zones"], cfg["primary_zones"]
    cc = pc["clean_day"]
    ov = zc["merge_overlap_min"]
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["qual"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    days["poc_pos"] = (days.poc - days.val) / (days.vah - days.val).replace(0, np.nan)
    days = days.merge(late_share(bars, days, tick, cc["late_from_period"]), left_on="date", right_index=True)
    days["clean"] = (days.qual & days.poc_pos.between(*cc["poc_pos_range"])
                     & (days.late_share <= cc["late_share_max"]))
    print(f"Зачётных дней {days.qual.sum()}, из них чистых {days.clean.sum()} "
          f"(POC в середине VA: {(days.qual & days.poc_pos.between(*cc['poc_pos_range'])).sum()}, "
          f"поздних TPO ≤ {cc['late_share_max']:.0%}: {(days.qual & (days.late_share <= cc['late_share_max'])).sum()})\n")

    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    rows = []
    for leave_by in ("hvn", "va"):
        for which in ("все", "чистые"):
            d = days.copy()
            d["ok"] = d.qual if which == "все" else d.clean
            z = zone_frame(d, daily, cfg, tick, leave_by)
            fz = z[z.available_from.notna()].copy()
            name = f"уход по {leave_by.upper()}, дни {which}"
            tag = f"{leave_by}_{'all' if which == 'все' else 'clean'}"
            zs = z.copy()
            zs["conf_dates"] = zs.conf_dates.map(lambda xs: " ".join(f"{x:%Y-%m-%d}" for x in xs))
            zs.to_csv(out / f"{instrument}_primary_zones_{tag}.csv", index=False, float_format="%.2f")

            qual = d[d.ok]
            real_conf = np.mean([len(x) > 0 for x in raw_confirmations(fz, qual, ov, tick)])
            td = zone_tests(daily, fz, tol)
            t30 = run_tests(bars, bands_for_30m(fz), cfg, tick)
            rng = np.random.default_rng(42)
            pconf, pday, p30h, p30r, p30f = [], [], [], [], []
            for r in range(pc["placebo_runs"]):
                pz = placebo(fz, rng, pc, tick, exclusive=False)
                pconf.append(np.mean([len(x) > 0 for x in raw_confirmations(pz, qual, ov, tick)]))
                pday.append((zone_tests(daily, pz, tol).result == "hold").mean())
                if r < pc["placebo_runs_30m"]:
                    pt = run_tests(bars, bands_for_30m(pz), cfg, tick)
                    s = m30(pt)
                    p30h.append(s["hold"])
                    pf = pt[(pt.test_no == 1) & (pt.flips_before == 0)]
                    p30f.append((pf.outcome == "hold").mean())
                    p30r.append(s["react16"])
            s30 = m30(t30)
            first30 = t30[(t30.test_no == 1) & (t30.flips_before == 0)]
            row = {
                "вариант": name, "зон": len(z),
                "многодневных": int((z.formation_days > 1).sum()),
                "дней формир. макс": int(z.formation_days.max()),
                "ширина мед., п.": z.width_pts.median(),
                "подтв. ≥1 (алг.)": f"{(fz.confirmations > 0).mean():.1%}",
                "подтв. сырые": fmt(real_conf, pconf),
                "день: n": len(td), "день: удерж.": fmt((td.result == "hold").mean(), pday),
                "30m: n": s30["n"], "30m: удерж.": fmt(s30["hold"], p30h),
                "30m: отход ≥16т": fmt(s30["react16"], p30r),
                "30m: 1-й тест удерж.": fmt((first30.outcome == "hold").mean(), p30f) + f" n={len(first30)}",
            }
            for role in ("support", "resistance"):
                tr = t30[t30.role == role]
                row[f"30m {role}"] = f"{(tr.outcome == 'hold').mean():.1%}"
            rows.append(row)
            print(name, "— готово")
    res = pd.DataFrame(rows)
    res.to_csv(out / f"{instrument}_primary_variants.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print("\n[в скобках — среднее у фальш-зон; ↑/↓ — вне 5–95% фальш-прогонов]\n")
    print(res.T.to_string(header=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
