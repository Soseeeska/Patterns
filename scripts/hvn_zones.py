"""Зоны из дневных HVN с 8–10 принтами в строке, со склейкой пересечений.

Цены — в back-adjusted ряду, т.е. в ценах текущего контракта выгрузки.

Запуск:  python -m scripts.hvn_zones [ES]
Роли: зоны, пересекающиеся ≥50%, но не склеенные из-за лимита ширины, —
верхняя «сопротивление», нижняя «поддержка»; остальные — «проторговка».

Результат: output/<INSTR>_hvn_day_zones.csv (зона каждого дня),
           output/<INSTR>_hvn_zones.csv (после склейки, с ролями)
"""
from __future__ import annotations

import sys

import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.hvn_zones import Zone, merge_zones, zone_clusters, zone_roles


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    zc = cfg["hvn_zones"]
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    _, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"])
    sel = days[days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])].sort_values("date")
    sel.to_csv(out / f"{instrument}_hvn_day_zones.csv", index=False, float_format="%.2f")

    day_zones = [Zone(int(round(r.band_lo / tick)), int(round(r.band_hi / tick)), [r.date])
                 for r in sel.itertuples()]
    max_w = int(round(zc["merge_max_width_points"] / tick))
    zones = merge_zones(day_zones, zc["merge_overlap_min"], max_w)
    zones.sort(key=lambda z: (z.lo, z.hi))
    roles, below, above = zone_roles(zones, zc["merge_overlap_min"])
    clusters = zone_clusters(zones, zc["merge_overlap_min"])

    trade_days = list(daily.date)
    nxt = {d: (trade_days[i + 1] if i + 1 < len(trade_days) else pd.NaT) for i, d in enumerate(trade_days)}
    res = pd.DataFrame([{
        "zone_id": k + 1, "cluster_id": clusters[k] + 1, "role": roles[k],
        "lo": z.lo * tick, "hi": z.hi * tick, "width_pts": (z.hi - z.lo) * tick,
        "n_days": len(z.dates), "first_day": z.dates[0], "last_day": z.dates[-1],
        "available_from": nxt[z.dates[0]],
        "under_zones": " ".join(str(i + 1) for i in below[k]),   # для них эта зона — сопротивление
        "over_zones": " ".join(str(i + 1) for i in above[k]),    # для них эта зона — поддержка
        "days": " ".join(f"{d:%Y-%m-%d}" for d in z.dates),
    } for k, z in enumerate(zones)])
    res.to_csv(out / f"{instrument}_hvn_zones.csv", index=False, float_format="%.2f")

    w = sel.band_hi - sel.band_lo
    print(f"{instrument}: дней с HVN {zc['min_tpo']}–{zc['max_tpo']} TPO: {len(sel)} из {len(days)}")
    print(f"Ширина дневной зоны: средняя {w.mean():.2f} п., медиана {w.median():.2f} п., "
          f"шире {zc['merge_max_width_points']:g} п.: {(w > zc['merge_max_width_points']).sum()} дней")
    print(f"После склейки: {len(res)} зон (склеено из ≥2 дней: {(res.n_days > 1).sum()}, "
          f"одиночных: {(res.n_days == 1).sum()})")
    print("Дней в зоне:", res.n_days.value_counts().sort_index().to_dict())
    print("Роли:", res.role.value_counts().to_dict())
    print("Роли среди склеенных (≥2 дней):", res[res.n_days > 1].role.value_counts().to_dict())
    print(f"Ширина зоны: средняя {res.width_pts.mean():.2f} п., медиана {res.width_pts.median():.2f} п.")

    last = daily.iloc[-1]
    near = res[(res.hi >= last.close - 150) & (res.lo <= last.close + 150)]
    cols = ["zone_id", "role", "lo", "hi", "width_pts", "n_days", "first_day", "last_day"]
    print(f"\nЗоны в ±150 п. от последнего close {last.close:.2f} ({last.date:%Y-%m-%d}):")
    print(near[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}",
                               formatters={"first_day": "{:%Y-%m-%d}".format, "last_day": "{:%Y-%m-%d}".format}))


if __name__ == "__main__":
    main(*sys.argv[1:])
