"""«Сила» первородных HVN-зон: предсказывает ли она следующий тест.

Метрики — только по истории до теста:
  n_tests    — сколько тестов зоны было раньше (30m, правила этапа 6);
  n_rej      — сколько баров-отторжений хвостом (лонг + шорт) было раньше;
  purity     — доля отторжений в одну сторону (max / сумма), если их ≥ 3;
  age        — дней с момента, как зона стала доступна;
  confirms   — сколько раз позже зачётный HVN снова лёг в зону (до теста);
  hvn_tpo    — сколько принтов было в HVN дня формирования (8–10);
  width      — ширина зоны, п.
Для каждой метрики — терцили (пороги по 2021–2024) и удержание следующего
теста; то же для сдвинутых зон (1.5–3 ширины, 5 прогонов) с пересчитанной
историей. Если у реальных зон разница «сильные − слабые» не больше, чем у
сдвинутых, метрика силы ничего не говорит о будущем.

Запуск:  python -m scripts.zone_strength [ES]
Результат: output/<INSTR>_zone_strength_tests.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, raw_confirmations, zone_frame
from scripts.stage1_profiles import load_adjusted
from scripts.zone_wicks import analyze
from tpo.data import ROOT, load_config
from tpo.zone_classes import interaction_types

METRICS = {"n_tests": "тестов до", "n_rej": "отторжений до", "purity": "чистота отторжений",
           "age": "возраст, дн.", "confirms": "подтверждений до", "hvn_tpo": "принтов в HVN", "width": "ширина, п."}


def strength_table(bars, zones, cfg, tick, qual):
    """Тесты зон с метриками силы на момент теста."""
    b = bars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    first = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    key = {(d, p): i for i, (d, p) in enumerate(zip(b.date, b.period))}
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    _, tests, _ = analyze(bars, zones, cfg, tick)
    tests = tests.sort_values(["zone_id", "date", "period"]).reset_index(drop=True)
    zi = zones.set_index("zone_id")
    conf = dict(zip(zones.zone_id, raw_confirmations(zones, qual, cfg["hvn_zones"]["merge_overlap_min"], tick)))
    rej_idx = {}
    for z in zones.itertuples():
        if z.available_from not in first.index:
            continue
        s = int(first[z.available_from])
        t = interaction_types(O[s:], H[s:], L[s:], C[s:], z.lo, z.hi, mw)
        rej_idx[z.zone_id] = (np.flatnonzero(t == "long") + s, np.flatnonzero(t == "short") + s)
    rows = []
    for zid, g in tests.groupby("zone_id"):
        z = zi.loc[zid]
        lg, sh = rej_idx.get(zid, (np.array([]), np.array([])))
        cd = np.sort(np.array(conf.get(zid, []), dtype="datetime64[ns]"))
        for n, t in enumerate(g.itertuples()):
            i = key[(t.date, t.period)]
            nl, ns = int(np.searchsorted(lg, i)), int(np.searchsorted(sh, i))
            rows.append({"zone_id": zid, "date": t.date, "outcome": t.outcome, "react": t.reaction_ticks,
                         "n_tests": n, "n_rej": nl + ns,
                         "purity": max(nl, ns) / (nl + ns) if nl + ns >= 3 else np.nan,
                         "age": (t.date - z.available_from).days,
                         "confirms": int(np.searchsorted(cd, np.datetime64(t.date))),
                         "hvn_tpo": z.hvn_tpo, "width": z.hi - z.lo})
    return pd.DataFrame(rows)


def tercile_hold(df, cuts):
    out = {}
    for m, (a, b) in cuts.items():
        s = df[df[m].notna()]
        lo, hi = s[s[m] <= a], s[s[m] > b]
        out[m] = ((lo.outcome == "hold").mean(), (hi.outcome == "hold").mean(), len(lo), len(hi))
    return out


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    zc, pc, cc = cfg["hvn_zones"], cfg["primary_zones"], cfg["day_context"]
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["ok"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    z = zone_frame(days, daily, cfg, tick)
    fz = z[z.available_from.notna()].reset_index(drop=True)
    fz["hvn_tpo"] = fz.start.map(days.set_index("date").hvn_tpo)
    qual = days[days.ok]
    split = pd.Timestamp(cc["split_date"])

    real = strength_table(bars, fz, cfg, tick, qual)
    real.to_csv(out / f"{instrument}_zone_strength_tests.csv", index=False)
    tr = real[real.date < split]
    cuts = {m: tuple(tr[m].dropna().quantile([1 / 3, 2 / 3])) for m in METRICS}
    rr = tercile_hold(real, cuts)
    rpre, rpost = tercile_hold(real[real.date < split], cuts), tercile_hold(real[real.date >= split], cuts)
    rng = np.random.default_rng(42)
    pl = []
    for _ in range(5):
        pz = placebo(fz, rng, pc, tick, exclusive=False).reset_index(drop=True)
        pz = pz.merge(fz[["zone_id", "hvn_tpo"]], on="zone_id")
        pl.append(tercile_hold(strength_table(bars, pz, cfg, tick, qual), cuts))

    print(f"{instrument}: тестов {len(real)}, удержание в среднем {(real.outcome == 'hold').mean():.1%}")
    print("Удержание теста: слабая треть → сильная треть (разница); у сдвинутых зон — разница (мин–макс из 5);"
          " до/после 2025\n")
    print(f"{'метрика':22s} {'пороги':>14s} {'слабые':>7s} {'сильные':>8s} {'разница':>8s} {'сдвинутые':>18s}"
          f"   {'разница до / после':>18s}")
    for m, nm in METRICS.items():
        a, b = cuts[m]
        lo, hi, nl, nh = rr[m]
        pd_ = [p[m][1] - p[m][0] for p in pl]
        d_pre = rpre[m][1] - rpre[m][0]
        d_post = rpost[m][1] - rpost[m][0]
        print(f"{nm:22s} {f'≤{a:.3g} / >{b:.3g}':>14s} {lo:7.1%} {hi:8.1%} {hi - lo:+8.1%} "
              f"{np.mean(pd_):+7.1%} ({min(pd_):+.1%}…{max(pd_):+.1%})   {d_pre:+7.1%} / {d_post:+.1%}"
              f"   n {nl}/{nh}")


if __name__ == "__main__":
    main(*sys.argv[1:])
