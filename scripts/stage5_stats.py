"""Этап 5: статистика взаимодействия с зонами + сравнение с фальш-зонами.

Запуск:  python -m scripts.stage5_stats [ES]
Результат: output/<INSTR>_stats.json (данные для отчёта), output/<INSTR>_placebo_events.csv
"""
from __future__ import annotations

import copy
import json
import math
import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_type import classify_days
from tpo.events import build_events
from tpo.stats import (TOUCH_ORDER, add_oriented, compare, placebo_distribution,
                       run_placebo, summarize)

RATE = ["acceptance", "reached_d0", "reached_d1", "reached_d3"]
ALL_M = RATE + ["mfe_atr", "mae_atr"]


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) else round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    return o


def table(df: pd.DataFrame, names: list[str]) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        key = r.pop("key")
        out.append({**dict(zip(names, key)), **r})
    return out


def filt(ev, cfg):
    ev = ev[~ev["breakout_day"]] if cfg["interaction"]["exclude_breakout_day"] else ev
    return add_oriented(ev)


def set_path(cfg, path, value):
    d = cfg
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value


PCT = {"acceptance", "reached_d0", "reached_d1", "reached_d3"}
SLICES = [  # (ключ в res, колонка группы, колонка подгруппы)
    ("overall", "all", None), ("by_level", "level_type", None),
    ("by_level_touch", "level_type", "touch_bucket"), ("by_level_approach", "level_type", "approach"),
    ("by_level_period", "level_type", "period"), ("by_level_rolls", "level_type", "rolls"),
    ("by_level_gap", "level_type", "gap_through"), ("by_day_type", "day_type", None),
]


def export_report_csv(res: dict, folder) -> None:
    """CSV для страницы отчёта. Доли — в процентах с 1 знаком, MFE/MAE — в ATR с 2 знаками,
    чтобы числа на странице совпадали с ячейками."""
    folder.mkdir(exist_ok=True)

    def fmt(m, v):
        if v is None:
            return None
        return round(100 * v, 1) if m in PCT else round(v, 2)

    rows = []
    for key, gcol, scol in SLICES:
        for r in res[key]:
            sub = "" if scol is None else str(r[scol])
            if scol == "gap_through":
                sub = "гэп через уровень" if r[scol] else "без гэпа"
            if scol == "approach":
                sub = "сверху" if r[scol] == "above" else "снизу"
            for m in ALL_M:
                lo, hi = r.get(m + "_placebo_range") or (None, None)
                ci = r.get(m + "_ci") or (None, None)
                rows.append({
                    "key": f"{key}|{r[gcol]}|{sub}|{m}", "slice": key, "group": r[gcol], "sub": sub,
                    "metric": m, "n": r["n"], "real": fmt(m, r[m]),
                    "ci_lo": fmt(m, ci[0]), "ci_hi": fmt(m, ci[1]),
                    "placebo": fmt(m, r.get(m + "_placebo")),
                    "placebo_lo": fmt(m, lo), "placebo_hi": fmt(m, hi),
                    "verdict": r.get(m + "_verdict"), "small": "да" if r["small"] else "нет",
                })
    pd.DataFrame(rows).to_csv(folder / "comparisons.csv", index=False)

    drows = []
    for dim, items in res["distributions"].items():
        for it in items:
            drows.append({"key": f"{dim}|{it['cat']}", "dim": dim, "cat": it["cat"],
                          **{k: (None if it[k] is None else round(100 * it[k], 1))
                             for k in ["all_days", "real", "real_rejection", "real_acceptance",
                                       "placebo", "placebo_lo", "placebo_hi"]},
                          "n_real": it["n_real"]})
    pd.DataFrame(drows).to_csv(folder / "distributions.csv", index=False)

    prow = []
    for it in res["prints"]:
        share = it["metric"] in ("tail_level_side", "poor_extreme_level_side")
        f = (lambda v: round(100 * v, 1)) if share else (lambda v: round(v, 2))
        prow.append({"metric": it["metric"], "label": it["label"], "unit": "%" if share else "",
                     "real_rejection": f(it["real_rejection"]), "placebo_rejection": f(it["placebo_rejection"]),
                     "placebo_rejection_lo": f(it["placebo_rejection_range"][0]),
                     "placebo_rejection_hi": f(it["placebo_rejection_range"][1]),
                     "real_acceptance": f(it["real_acceptance"]), "placebo_acceptance": f(it["placebo_acceptance"]),
                     "placebo_acceptance_lo": f(it["placebo_acceptance_range"][0]),
                     "placebo_acceptance_hi": f(it["placebo_acceptance_range"][1])})
    pd.DataFrame(prow).to_csv(folder / "prints.csv", index=False)

    srows = []
    for it in res["sensitivity"]:
        srows.append({"key": f"{it['param']}|{it['value']}|{it['level_type']}", **{
            k: (round(100 * v, 1) if k in ("acceptance", "reached_d0", "acceptance_virgin", "reached_d0_virgin")
                and v is not None else v) for k, v in it.items()}})
    pd.DataFrame(srows).to_csv(folder / "sensitivity.csv", index=False)

    cmp = pd.DataFrame(rows)
    judged = cmp[(cmp.small == "нет") & cmp.metric.isin(["acceptance", "reached_d0"])
                 & (cmp.slice != "overall") & (cmp.slice != "by_level")]
    m = res["meta"]
    pd.DataFrame([{
        "start": m["start"], "end": m["end"], "days": m["days"], "composites": m["composites"],
        "composites_all": m["composites_all"], "min_composite_days": m["min_composite_days"],
        "zones": m["zones"], "events_total": m["events_total"],
        "events_breakout_day": m["events_breakout_day"], "events": m["events"],
        "event_days": m["event_days"], "event_days_pct": round(100 * m["event_days"] / m["days"], 1),
        "placebo_runs": m["placebo_runs"], "placebo_events_mean": round(m["placebo_events_mean"]),
        "rolls": m["rolls"], "cells_judged": len(judged),
        "cells_outside": int((judged.verdict != "как фон").sum()),
        "cells_outside_pct": round(100 * (judged.verdict != "как фон").mean(), 1),
    }]).to_csv(folder / "summary.csv", index=False)


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    min_n = cfg["stats"]["min_events_per_cell"]
    holdout = pd.Timestamp(cfg["stats"]["holdout_from"])

    bars, daily, profiles, rolls, tick = load_adjusted(instrument, cfg)
    days = classify_days(daily, profiles, cfg, tick)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    real_all = build_events(bars, days, comps, rolls, cfg, tick)
    real = filt(real_all, cfg)
    print(f"Реальных событий: {len(real)}; считаю {cfg['stats']['placebo']['runs']} прогонов фальш-зон…")
    placebo = [filt(p, cfg) for p in run_placebo(bars, days, comps, rolls, cfg, tick)]
    pd.concat(placebo).to_csv(out / f"{instrument}_placebo_events.csv", index=False, float_format="%.3f")

    for e in [real, *placebo]:
        e["period"] = np.where(e["date"] < holdout, f"до {holdout:%Y}", f"с {holdout:%Y}")
        e["rolls"] = np.where(e["rolls_crossed"] == 0, "в том же контракте", "через ролл")

    res: dict = {"instrument": instrument}
    full = days[days.day_type != "Short"]
    res["meta"] = {
        "start": full.date.min(), "end": full.date.max(), "days": len(full),
        "composites": int(((comps.end_reason != "open")
                           & (comps.n_days >= cfg["zones"].get("min_composite_days", 0))).sum()),
        "composites_all": int((comps.end_reason != "open").sum()),
        "min_composite_days": cfg["zones"].get("min_composite_days", 0),
        "zones": int(real_all.zone_id.nunique()),
        "events_total": len(real_all), "events_breakout_day": int(real_all.breakout_day.sum()),
        "events": len(real), "event_days": int(real.date.nunique()),
        "placebo_runs": len(placebo),
        "placebo_events_mean": float(np.mean([len(p) for p in placebo])),
        "min_n": min_n, "holdout_from": holdout,
        "rolls": len(rolls),
    }

    for e in [real, *placebo]:
        e["all"] = "все касания"
    res["overall"] = table(compare(real, placebo, ["all"], ALL_M, min_n), ["all"])
    res["by_level"] = table(compare(real, placebo, ["level_type"], ALL_M, min_n), ["level_type"])
    res["by_level_touch"] = table(compare(real, placebo, ["level_type", "touch_bucket"], ALL_M, min_n),
                                  ["level_type", "touch_bucket"])
    res["by_level_approach"] = table(compare(real, placebo, ["level_type", "approach"], ALL_M, min_n),
                                     ["level_type", "approach"])
    res["by_level_period"] = table(compare(real, placebo, ["level_type", "period"], ALL_M, min_n),
                                   ["level_type", "period"])
    res["by_level_rolls"] = table(compare(real, placebo, ["level_type", "rolls"], ALL_M, min_n),
                                  ["level_type", "rolls"])
    res["by_level_gap"] = table(compare(real, placebo, ["level_type", "gap_through"], ALL_M, min_n),
                                ["level_type", "gap_through"])

    # Распределения: тип дня, форма, миграция value
    dists = {}
    for col in ["day_type", "profile_shape", "value_dir"]:
        base = full[col].value_counts(normalize=True) if col in full else None
        rd = real[col].value_counts(normalize=True)
        rej = real[real.outcome == "rejection"][col].value_counts(normalize=True)
        acc = real[real.outcome == "acceptance"][col].value_counts(normalize=True)
        pdist = placebo_distribution(placebo, col)
        cats = sorted(set(rd.index) | set(pdist.index) | (set(base.index) if base is not None else set()))
        dists[col] = [{
            "cat": c,
            "all_days": None if base is None else float(base.get(c, 0)),
            "real": float(rd.get(c, 0)),
            "real_rejection": float(rej.get(c, 0)),
            "real_acceptance": float(acc.get(c, 0)),
            "placebo": float(pdist["mean"].get(c, 0)),
            "placebo_lo": float(pdist["lo"].get(c, 0)),
            "placebo_hi": float(pdist["hi"].get(c, 0)),
            "n_real": int((real[col] == c).sum()),
        } for c in cats]
    res["distributions"] = dists

    # Исход в разрезе типа дня (тип дня → доля отказов), реальные vs фальш
    res["by_day_type"] = table(compare(real, placebo, ["day_type"], ALL_M, min_n), ["day_type"])

    # Метрики принтов: медианы/доли для отказа и принятия, реальные vs фальш
    pm = {
        "close_pos_dir": ("Close в сторону подхода (0–1)", "median"),
        "poc_pos_dir": ("POC в сторону подхода (0–1)", "median"),
        "tpo_share_dir": ("Доля TPO со стороны подхода от POC", "median"),
        "tail_level_side": ("Хвост со стороны уровня, доля дней с хвостом", "share_pos"),
        "poor_extreme_level_side": ("Poor high/low со стороны уровня, доля", "mean"),
        "single_prints": ("Single prints, строк (медиана)", "median"),
        "va_width_rel": ("Ширина VA к средней (медиана)", "median"),
    }

    def agg(s, how):
        s = s.astype(float)
        return s.median() if how == "median" else ((s > 0).mean() if how == "share_pos" else s.mean())

    prints = []
    for col, (label, how) in pm.items():
        row = {"metric": col, "label": label}
        for oc in ["rejection", "acceptance"]:
            row[f"real_{oc}"] = agg(real[real.outcome == oc][col], how)
            vals = [agg(p[p.outcome == oc][col], how) for p in placebo]
            row[f"placebo_{oc}"] = float(np.mean(vals))
            row[f"placebo_{oc}_range"] = [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]
        row["all_days"] = None
        prints.append(row)
    res["prints"] = prints

    # Чувствительность к порогам (только реальные зоны)
    sens = []
    for path, values in cfg["stats"]["sensitivity"].items():
        for v in values:
            c2 = copy.deepcopy(cfg)
            set_path(c2, path, v)
            comps2, _ = find_balances(bars, daily, profiles, c2, tick)
            ev2 = filt(build_events(bars, days, comps2, rolls, c2, tick), c2)
            s_all = summarize(ev2, ["level_type"])
            s_v = summarize(ev2[ev2.touch_number == 1], ["level_type"])
            for lv in s_all.index:
                sens.append({
                    "param": path, "value": v, "level_type": lv,
                    "composites": int(((comps2.end_reason != "open")
                                       & (comps2.n_days >= c2["zones"].get("min_composite_days", 0))).sum()),
                    "n": int(s_all.at[lv, "n"]),
                    "acceptance": s_all.at[lv, "acceptance"],
                    "reached_d0": s_all.at[lv, "reached_d0"],
                    "n_virgin": int(s_v.at[lv, "n"]) if lv in s_v.index else 0,
                    "acceptance_virgin": s_v.at[lv, "acceptance"] if lv in s_v.index else np.nan,
                    "reached_d0_virgin": s_v.at[lv, "reached_d0"] if lv in s_v.index else np.nan,
                })
            print(f"  чувствительность {path}={v}: событий {len(ev2)}")
    res["sensitivity"] = sens
    res["touch_order"] = TOUCH_ORDER

    with open(out / f"{instrument}_stats.json", "w", encoding="utf-8") as f:
        json.dump(clean(res), f, ensure_ascii=False, indent=1)
    export_report_csv(clean(res), out / "report")

    # Краткая печать
    pd.set_option("display.width", 220)
    t = pd.DataFrame(res["by_level_touch"])
    print(t[["level_type", "touch_bucket", "n", "acceptance", "acceptance_placebo",
             "acceptance_verdict", "reached_d0", "reached_d0_placebo", "reached_d0_verdict"]]
          .round(3).to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
