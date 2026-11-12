"""Подгонка границ первородных зон по краям тел 30m-свечей и проверка.

Зоны — по RTH-профилям (как раньше), свечи — набор баров для тестов
(по умолчанию ES_ETH, ETH+RTH). Для каждой зоны:
  * подгонка (tpo/zone_align.py) — по барам первых calib_sessions сессий,
    начиная с дня доступности зоны;
  * проверка — только на барах после этого окна (без подглядывания).
Сравниваются: исходные зоны, подогнанные зоны и фальш-зоны (сдвиг на
1.5–3 ширины), подогнанные тем же способом — подгонка сама по себе
притягивает любую полосу к скоплениям тел, поэтому честное сравнение —
«подогнанные реальные против подогнанных фальш».

Метрики на проверочном отрезке (как в scripts/zone_wicks.py) плюс доля
баров-касаний, у которых край тела на границе зоны (± 1 тик).

Запуск:  python -m scripts.zone_align_test [ES] [бары: ES_ETH]
Результат: output/<TAG>_zone_align.csv (зоны до/после), output/<TAG>_zone_align_summary.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo, zone_frame
from scripts.stage1_profiles import align_zones, load_adjusted, load_test_bars, tag
from scripts.zone_wicks import analyze, summary
from tpo.data import ROOT, load_config
from tpo.zone_align import align_zone

KEYS = [("A: main_boundary", "основной хвост через границу"),
        ("A: opp_boundary", "противоположный хвост через границу"),
        ("A: close_inside", "close внутри зоны"),
        ("body_edge", "край тела на границе (± 1 тик)"),
        ("B: все тесты | удерж.", "удержание, все тесты"),
        ("B: все тесты | отход≥16т", "отход ≥ 16 тиков"),
        ("B: 1-й бар: отторжение хвостом | удерж.", "удержание после отторжения хвостом"),
        ("B:   противоположный хвост | удерж.", "  … противоположным хвостом"),
        ("B: 1-й бар: без отторжения | удерж.", "удержание без отторжения"),
        ("B: все тесты | n", "тестов")]


def calibrate(zones: pd.DataFrame, bars: pd.DataFrame, cfg: dict, tick: float, do_align: bool) -> pd.DataFrame:
    """Подгоняет зоны по калибровочному окну; available_from -> начало проверки."""
    ac = cfg["zone_align"]
    b = bars.sort_values("ts").reset_index(drop=True)
    sessions = np.sort(b.date.unique())
    first = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    rows = []
    for z in zones.itertuples():
        i0 = np.searchsorted(sessions, np.datetime64(z.available_from))
        i1 = i0 + ac["calib_sessions"]
        if i1 >= len(sessions):
            continue
        s0, s1 = int(first[sessions[i0]]), int(first[sessions[i1]])
        lo, hi, s_lo, s_hi = z.lo, z.hi, np.nan, np.nan
        if do_align:
            search = max(ac["search_frac"] * (z.hi - z.lo), ac["search_min_points"])
            lo, hi, s_lo, s_hi = align_zone(O[s0:s1], H[s0:s1], L[s0:s1], C[s0:s1], z.lo, z.hi, tick, search, mw,
                                            ac["w_plain"], ac["min_score"], ac["min_width_ticks"] * tick,
                                            ac["max_width_points"])
        rows.append({"zone_id": z.zone_id, "lo_orig": z.lo, "hi_orig": z.hi, "lo": lo, "hi": hi,
                     "score_lo": s_lo, "score_hi": s_hi, "available_from": sessions[i1]})
    return pd.DataFrame(rows)


def body_edge_share(bars: pd.DataFrame, zones: pd.DataFrame, tick: float) -> float:
    b = bars.sort_values("ts").reset_index(drop=True)
    first = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    top, bot = np.maximum(O, C), np.minimum(O, C)
    hits = n = 0
    for z in zones.itertuples():
        s = int(first[z.available_from])
        t = (L[s:] <= z.hi) & (H[s:] >= z.lo)
        e = np.zeros(len(t), bool)
        for edge in (z.lo, z.hi):
            e |= (np.abs(top[s:] - edge) <= tick + 1e-9) | (np.abs(bot[s:] - edge) <= tick + 1e-9)
        hits += int((t & e).sum())
        n += int(t.sum())
    return hits / n if n else np.nan


def evaluate(bars, zones, cfg, tick) -> dict:
    tch, tests, _ = analyze(bars, zones, cfg, tick)
    s = summary(tch, tests)
    s["body_edge"] = body_edge_share(bars, zones, tick)
    return s


def main(instrument: str = "ES", bars_instrument: str = "ES_ETH") -> None:
    cfg = load_config(instrument=instrument)
    zc, pc, ac = cfg["hvn_zones"], cfg["primary_zones"], cfg["zone_align"]
    out = ROOT / "output"
    rbars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["ok"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    z = zone_frame(days, daily, cfg, tick)
    fz = z[z.available_from.notna()].reset_index(drop=True)
    bars = load_test_bars(bars_instrument)[0] if bars_instrument != instrument else rbars
    fz = align_zones(fz, bars).reset_index(drop=True)
    name = tag(instrument, bars_instrument, None)

    orig = calibrate(fz, bars, cfg, tick, do_align=False)
    alig = calibrate(fz, bars, cfg, tick, do_align=True)
    alig.to_csv(out / f"{name}_zone_align.csv", index=False, float_format="%.2f")
    moved = (alig.lo != alig.lo_orig) | (alig.hi != alig.hi_orig)
    print(f"{name}: зон с проверочным отрезком {len(alig)}; границы сдвинуты у {moved.sum()} зон "
          f"(верх {(alig.hi != alig.hi_orig).sum()}, низ {(alig.lo != alig.lo_orig).sum()})")
    d_hi, d_lo = (alig.hi - alig.hi_orig)[moved], (alig.lo - alig.lo_orig)[moved]
    print(f"  сдвиг верха: медиана |{d_hi.abs().median():.2f}| п., низа: |{d_lo.abs().median():.2f}| п.; "
          f"ширина: была {(alig.hi_orig - alig.lo_orig).median():.2f} п., стала {(alig.hi - alig.lo).median():.2f} п.")

    res = {"исходные": evaluate(bars, orig, cfg, tick), "подогнанные": evaluate(bars, alig, cfg, tick)}
    rng = np.random.default_rng(42)
    p_orig, p_alig = [], []
    for _ in range(ac["placebo_runs"]):
        pz = placebo(fz, rng, pc, tick, exclusive=False).reset_index(drop=True)
        p_orig.append(evaluate(bars, calibrate(pz, bars, cfg, tick, False), cfg, tick))
        p_alig.append(evaluate(bars, calibrate(pz, bars, cfg, tick, True), cfg, tick))

    rows = []
    print(f"\n{'метрика':40s} {'исходные':>9s} {'фальш':>7s} {'подогн.':>9s} {'фальш подогн.':>14s}")
    for k, nm in KEYS:
        po = np.array([p[k] for p in p_orig], float)
        pa = np.array([p[k] for p in p_alig], float)
        ro, ra = res["исходные"][k], res["подогнанные"][k]
        if k.endswith("| n"):
            print(f"{nm:40s} {ro:9.0f} {po.mean():7.0f} {ra:9.0f} {pa.mean():14.0f}")
        else:
            lo, hi = np.percentile(pa, [5, 95])
            mark = " ↑" if ra > hi else (" ↓" if ra < lo else "")
            print(f"{nm:40s} {ro:9.1%} {po.mean():7.1%} {ra:9.1%} {pa.mean():8.1%} ({lo:.1%}–{hi:.1%}){mark}")
        rows.append({"metric": nm, "orig": ro, "orig_placebo": po.mean(), "aligned": ra,
                     "aligned_placebo": pa.mean(), "aligned_placebo_p5": np.percentile(pa, 5),
                     "aligned_placebo_p95": np.percentile(pa, 95)})
    pd.DataFrame(rows).to_csv(out / f"{name}_zone_align_summary.csv", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
