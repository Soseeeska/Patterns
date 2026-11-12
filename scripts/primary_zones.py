"""Первородные HVN-зоны: фиксация после ухода цены, подтверждения, тесты П/С.

Логика — в tpo/primary_zones.py. Сравнение с фальш-зонами: каждая
зафиксированная зона сдвигается на U(1.5, 3) своей ширины вверх или вниз
(дополнительно — вариант, где фальш-зона не пересекается ни с одной
реальной); для фальш-зон
считаются те же тесты и «сырые» подтверждения (зачётные дни после
фиксации, полоса которых пересекается с зоной ≥ 50%).

Запуск:  python -m scripts.primary_zones [ES]
Результат: output/<INSTR>_primary_zones.csv, output/<INSTR>_primary_zone_tests.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.primary_zones import build_primary_zones, zone_tests


def raw_confirmations(z: pd.DataFrame, qual: pd.DataFrame, overlap_min: float, tick: float) -> list[list]:
    """Зачётные дни после available_from, полоса которых пересекается с зоной ≥ overlap_min."""
    qd = qual.date.to_numpy()
    qlo, qhi = qual.band_lo.to_numpy(), qual.band_hi.to_numpy()
    out = []
    for r in z.itertuples():
        inter = np.minimum(qhi, r.hi) - np.maximum(qlo, r.lo) + tick
        narrow = np.minimum(qhi - qlo, r.hi - r.lo) + tick
        m = (inter / narrow >= overlap_min - 1e-9) & (qd >= np.datetime64(r.available_from))
        out.append(list(qd[m]))
    return out


def placebo(z: pd.DataFrame, rng, pc: dict, tick: float, exclusive: bool) -> pd.DataFrame:
    """Сдвиг каждой зоны на U(a, b) ширин вверх/вниз (со своей зоной не пересекается).
    exclusive=True — ещё и ни с одной реальной зоной (до 50 попыток, иначе зона выпадает;
    выживают зоны в «пустых» местах, поэтому этот вариант занижает фон)."""
    lo_all, hi_all = z.lo.to_numpy(), z.hi.to_numpy()
    rows = []
    for r in z.itertuples():
        w = max(r.hi - r.lo, pc["placebo_min_shift_points"])
        for _ in range(50 if exclusive else 1):
            shift = rng.choice([-1, 1]) * rng.uniform(*pc["placebo_shift_widths"]) * w
            shift = round(shift / tick) * tick
            lo, hi = r.lo + shift, r.hi + shift
            if not exclusive or not ((lo_all <= hi) & (hi_all >= lo)).any():
                rows.append({"zone_id": r.zone_id, "lo": lo, "hi": hi, "available_from": r.available_from})
                break
    return pd.DataFrame(rows)


def test_summary(t: pd.DataFrame) -> dict:
    """Доли исходов в разных срезах."""
    out = {}
    cuts = {
        "все": t,
        "поддержка": t[t.role == "support"],
        "сопротивление": t[t.role == "resistance"],
        "1-й тест": t[t.test_n == 1],
        "2–3-й тест": t[t.test_n.between(2, 3)],
        "4+ тест": t[t.test_n >= 4],
        "до пробоя": t[~t.after_break],
        "после пробоя (флип)": t[t.after_break],
        "0 подтверждений к тесту": t[t.confirmations_before == 0],
        "≥1 подтверждение к тесту": t[t.confirmations_before >= 1],
    }
    for name, s in cuts.items():
        n = len(s)
        out[name] = {"n": n, **{k: (s.result == k).mean() if n else np.nan for k in ("hold", "break", "inside")}}
    return out


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    zc, pc = cfg["hvn_zones"], cfg["primary_zones"]
    ov = zc["merge_overlap_min"]
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    _, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["ok"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    inp = pd.DataFrame({"date": days.date, "lo": np.rint(days.band_lo / tick).astype(int),
                        "hi": np.rint(days.band_hi / tick).astype(int), "ok": days.ok})
    zones = build_primary_zones(inp, ov, int(round(zc["merge_max_width_points"] / tick)))

    trade_days = list(daily.date)
    nxt = {d: trade_days[i + 1] for i, d in enumerate(trade_days[:-1])}
    z = pd.DataFrame([{
        "zone_id": k + 1, "lo": p.lo * tick, "hi": p.hi * tick, "width_pts": (p.hi - p.lo) * tick,
        "formation_days": len(p.days), "start": p.days[0], "last_formation_day": p.days[-1],
        "fixed_on": p.fixed_on, "available_from": nxt.get(p.fixed_on, pd.NaT) if p.fixed_on is not None else pd.NaT,
        "confirmations": len(p.confirmations),
        "first_confirmation": p.confirmations[0] if p.confirmations else pd.NaT,
        "conf_dates": p.confirmations,
    } for k, p in enumerate(zones)])
    fz = z[z.available_from.notna()].copy()

    qual = days[days.ok]
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    tests = zone_tests(daily, fz, tol)
    tests = tests.merge(fz[["zone_id", "formation_days"]], on="zone_id")
    real_raw = raw_confirmations(fz, qual, ov, tick)

    rng = np.random.default_rng(42)
    p_tests, p_conf, p_conf_ex, p_n_ex = [], [], [], []
    for _ in range(pc["placebo_runs"]):
        pz = placebo(fz, rng, pc, tick, exclusive=False)
        pz["conf_dates"] = raw_confirmations(pz, qual, ov, tick)
        p_conf.append(np.mean([len(x) > 0 for x in pz.conf_dates]))
        p_tests.append(zone_tests(daily, pz, tol))
        px = placebo(fz, rng, pc, tick, exclusive=True)
        p_conf_ex.append(np.mean([len(x) > 0 for x in raw_confirmations(px, qual, ov, tick)]))
        p_n_ex.append(len(px))

    zsave = z.copy()
    zsave["conf_dates"] = zsave.conf_dates.map(lambda xs: " ".join(f"{d:%Y-%m-%d}" for d in xs))
    zsave.to_csv(out / f"{instrument}_primary_zones.csv", index=False, float_format="%.2f")
    tests.to_csv(out / f"{instrument}_primary_zone_tests.csv", index=False)

    q = days.ok.sum()
    print(f"{instrument}: зачётных дней {q}, первородных зон {len(z)} (зафиксировано {len(fz)})")
    print(f"  дни формирования: {z.formation_days.value_counts().sort_index().to_dict()}")
    print(f"  ширина: медиана {z.width_pts.median():.2f} п., средняя {z.width_pts.mean():.2f} п.")
    used_conf = z.confirmations.sum()
    print(f"  зачётные дни: в формировании {z.formation_days.sum()}, подтверждения {used_conf}")
    print("\nПодтверждения (по алгоритму):")
    print(f"  зон с ≥1 подтверждением: {(fz.confirmations > 0).mean():.1%}  "
          f"распределение: {fz.confirmations.value_counts().sort_index().to_dict()}")
    gap = (fz.first_confirmation - fz.fixed_on).dt.days.dropna()
    print(f"  до первого подтверждения: медиана {gap.median():.0f} дн., 25–75%: "
          f"{gap.quantile(.25):.0f}–{gap.quantile(.75):.0f} дн.")
    by_f = fz.groupby(fz.formation_days.clip(upper=3)).confirmations.agg(["size", lambda s: (s > 0).mean(), "mean"])
    by_f.columns = ["зон", "доля с подтв.", "среднее подтв."]
    by_f.index = by_f.index.map({1: "1 день", 2: "2 дня", 3: "3+ дня"})
    print(by_f.round(2).to_string())
    print(f"  «сырое» подтверждение (любой зачётный день в зоне ≥50%): реальные "
          f"{np.mean([len(x) > 0 for x in real_raw]):.1%} (в среднем {np.mean([len(x) for x in real_raw]):.2f}), "
          f"фальш {np.mean(p_conf):.1%} ({np.percentile(p_conf, 5):.1%}–{np.percentile(p_conf, 95):.1%}); "
          f"фальш вне реальных зон {np.mean(p_conf_ex):.1%} (выжило ~{np.mean(p_n_ex):.0f} зон)")

    rs = test_summary(tests)
    ps = [test_summary(t) for t in p_tests]
    print(f"\nТесты зон дневными барами (касание ±{tol:g} п., подход по open): "
          f"удержание / пробой / принятие внутри; [фальш: удержание среднее (5–95%)]")
    for name, v in rs.items():
        ph = [p[name]["hold"] for p in ps if p[name]["n"]]
        pn = np.mean([p[name]["n"] for p in ps])
        print(f"  {name:26s} n={v['n']:5d}  {v['hold']:.1%} / {v['break']:.1%} / {v['inside']:.1%}   "
              f"[фальш {np.mean(ph):.1%} ({np.percentile(ph, 5):.1%}–{np.percentile(ph, 95):.1%}), n≈{pn:.0f}]")


if __name__ == "__main__":
    main(*sys.argv[1:])
