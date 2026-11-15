"""Участки графика «как в жизни»: где строятся композиты и где дни остаются сами по себе.

Для каждого примера — 30m-свечи RTH окна, профиль TPO и VA / POC каждой сессии,
композиты (от 2 дней), попавшие в окно, и зоны карты так, как они были известны
в каждую сессию (scripts/day_context.active: класс HVN на дату, молодая
первородная — серая). Для каждой зоны — откуда она взялась.

Примеры:
  comp — композиты 3 / 5 / 11 / 20 дней (с соседними двухдневными);
  none — отрезки, где ни один композит не собрался (каждый день закрывался
         вне диапазона предыдущих).

Запуск:  python -m scripts.zone_examples_export [ES]
Результат: output/zone_examples.html (шаблон scripts/zone_examples_template.html)
"""
from __future__ import annotations

import copy
import json
import sys

import numpy as np
import pandas as pd

from scripts.day_context import active, build_zone_map, hvn_class_table
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import hvn_band

EXAMPLES = [  # (группа, первый день композита / отрезка, последний день)
    ("comp", "2026-10-09", "2026-10-13"),
    ("comp", "2026-08-20", "2026-08-26"),
    ("comp", "2026-09-21", "2026-10-05"),
    ("comp", "2026-02-12", "2026-03-12"),
    ("none", "2025-09-24", "2025-09-30"),
    ("none", "2024-03-20", "2024-03-26"),
    ("none", "2023-10-25", "2023-11-02"),
    ("none", "2023-01-26", "2023-02-06"),
]
BEFORE, AFTER = 3, 8          # сессий до и после
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def dm(d) -> str:
    return f"{pd.Timestamp(d):%d.%m}"


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    share = cfg["wicks"]["class_rejection_share"]
    hs = cfg["intraday"]["hvn_share"]
    zmap, prim, dd = build_zone_map(cfg, bars, daily, profiles, tick)
    cls_tab = hvn_class_table(bars, prim, cfg["wicks"]["min_wick_ticks"] * tick, share)

    # композиты от 2 дней с их HVN-полосой
    c2 = copy.deepcopy(cfg)
    c2["balance"]["min_days"] = 2
    comps, cprof = find_balances(bars, daily, profiles, c2, tick)
    comps = comps[comps.end_reason != "open"].copy()
    bl, bh = [], []
    for r in comps.itertuples():
        lo, hi = hvn_band(cprof[r.composite_id], int(round(r.poc / tick)), hs)
        bl.append(lo * tick)
        bh.append(hi * tick)
    comps["blo"], comps["bhi"] = bl, bh
    min_comp = cfg["zones"]["min_composite_days"]

    # слабые HVN-зоны — для описания происхождения серых
    gc = cfg["grey_zones"]
    w = dd.drop(columns="ok").copy()
    w["ok"] = w.hvn_tpo.between(*gc["hvn_tpo"])
    weak = zone_frame(w, daily, cfg, tick)
    tpo_by_day = dict(zip(dd.date, dd.hvn_tpo))

    def origin(z) -> dict:
        af = z.available_from
        if z.type == "old_balance" or (z.type == "grey" and z.src == "comp"):
            m = comps[(comps.available_from == af) & (np.isclose(comps.val, z.lo) & np.isclose(comps.vah, z.hi)
                                                     | np.isclose(comps.blo, z.lo) & np.isclose(comps.bhi, z.hi))]
            if len(m):
                c = m.iloc[0]
                what = "VA композита" if z.type == "old_balance" else "HVN-полоса композита"
                return {"t": f"{what} {c.n_days} дн. {dm(c.start)}–{dm(c.end)}"}
        if z.type == "hvn":
            p = prim[prim.zone_id == z.zone_id].iloc[0]
            days = ", ".join(f"{dm(d)} ({tpo_by_day.get(d, '?')} TPO)" for d in [p.start, p.last_formation_day][:1 if p.formation_days == 1 else 2])
            return {"t": f"первородная HVN-зона: формирование {days}, {p.formation_days} дн., "
                         f"цена ушла {dm(p.fixed_on)}",
                    "cf": [f"{pd.Timestamp(d):%Y-%m-%d}" for d in p.conf_dates], "fd": int(p.formation_days)}
        if z.type == "grey" and z.src == "hvn":
            m = weak[(weak.available_from == af) & np.isclose(weak.lo, z.lo) & np.isclose(weak.hi, z.hi)]
            if len(m):
                p = m.iloc[0]
                return {"t": f"слабый HVN-день: {dm(p.start)} ({tpo_by_day.get(p.start, '?')} TPO), "
                             f"{p.formation_days} дн. формирования, цена ушла {dm(p.fixed_on)}"}
        return {"t": "—"}

    full = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    fdates = list(full.date)
    by_day = {k: g.sort_values("period") for k, g in bars.groupby("date")}
    examples = []
    for group, a, b in EXAMPLES:
        ia, ib = fdates.index(pd.Timestamp(a)), fdates.index(pd.Timestamp(b))
        win = fdates[max(0, ia - BEFORE): min(len(fdates), ib + AFTER + 1)]
        pos = {d: i for i, d in enumerate(win)}
        days, bars_out = [], []
        for si, d in enumerate(win):
            r = full[full.date == d].iloc[0]
            p = profiles[d]
            blo, bhi = hvn_band(p, int(round(r.poc / tick)), hs)
            days.append({"date": f"{d:%Y-%m-%d}", "lab": f"{WD[d.weekday()]} {dm(d)}",
                         "vah": float(r.vah), "val": float(r.val), "poc": float(r.poc),
                         "blo": blo * tick, "bhi": bhi * tick, "tpo": int(tpo_by_day.get(d, 0)),
                         "pl": p.low * tick, "pr": p.row * tick, "pc": [int(x) for x in p.counts]})
            for g in by_day[d].itertuples():
                bars_out.append([g.open, g.high, g.low, g.close, si])
        lo_p = min(x[2] for x in bars_out) - 6
        hi_p = max(x[1] for x in bars_out) + 6

        cw = comps[(comps.end >= win[0]) & (comps.start <= win[-1])]
        comps_out = [{"s": pos.get(c.start, -1), "e": pos.get(c.end, len(win)), "n": int(c.n_days),
                      "start": dm(c.start), "end": dm(c.end),
                      "hi": c.high, "lo": c.low, "vah": c.vah, "val": c.val, "poc": c.poc,
                      "blo": c.blo, "bhi": c.bhi, "main": bool(c.n_days >= min_comp)} for c in cw.itertuples()]

        zinfo, zs = {}, []
        for d in win:
            act = active(zmap, d, cls_tab, share)
            act = act[(act.hi >= lo_p) & (act.lo <= hi_p)]
            row = []
            for k, z in act.iterrows():
                key = str(k)
                if key not in zinfo:
                    base = zmap.loc[k]
                    zinfo[key] = {"lo": float(base.lo), "hi": float(base.hi), "av": f"{base.available_from:%Y-%m-%d}",
                                  "src": base.src, **origin(base)}
                kind = z.type if z.type != "grey" else f"grey_{z.src}"
                row.append([key, kind])
            zs.append(row)

        # подпись
        if group == "comp":
            c = comps[comps.start == pd.Timestamp(a)].iloc[0]
            title = f"Композит {c.n_days} дн. · {dm(c.start)}–{dm(c.end)}.{pd.Timestamp(a):%Y}"
        else:
            title = f"Без композитов · {dm(a)}–{dm(b)}.{pd.Timestamp(b):%Y}"
        born = [v for v in zinfo.values() if pd.Timestamp(v["av"]) >= win[0]]
        cnt = pd.Series([v["src"] if v["src"] != "hvn" or "первородная" in v["t"] else "weak" for v in born]).value_counts()
        nm = {"comp": "от композитов", "hvn": "первородных HVN", "weak": "слабых HVN", "young": "молодых"}
        new = ", ".join(f"{nm.get(k, k)} {v}" for k, v in cnt.items()) or "нет"
        n2 = int(((cw.n_days < min_comp)).sum())
        n3 = int(((cw.n_days >= min_comp)).sum())
        note = (f"{len(win)} сессий {dm(win[0])}–{dm(win[-1])}. Композитов в окне: от {min_comp} дн. — {n3}, "
                f"двухдневных — {n2}. Новых зон за окно: {new}.")
        if group == "none":
            note += f" {dm(a)}–{dm(b)} ни один день не закрылся внутри диапазона предыдущих: TPO каждого дня остаётся в своей сессии."
        examples.append({"id": f"{group}-{a}", "group": group, "title": title, "note": note,
                         "core": [pos[pd.Timestamp(a)], pos[pd.Timestamp(b)]],
                         "lo": lo_p, "hi": hi_p, "days": days, "bars": bars_out, "comps": comps_out,
                         "zones": zinfo, "zs": zs})
        print(title, "|", note)

    data = {"instrument": "ES (ESZ6, back-adjusted), RTH 30m", "tick": tick, "examples": examples,
            "grey_hvn_tpo": gc["hvn_tpo"], "min_history": gc["min_history_days"]}
    raw = json.dumps(data, separators=(",", ":"), default=float)
    tpl = (ROOT / "scripts" / "zone_examples_template.html").read_text(encoding="utf-8")
    path = ROOT / "output" / "zone_examples.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text(tpl.replace("__DATA__", raw.replace("</", "<\\/")), encoding="utf-8")
    print(path, f"{len(raw) / 1e6:.2f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
