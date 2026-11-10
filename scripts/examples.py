"""5 визуальных примеров касаний зон прошлых композитов.

Запуск:  python -m scripts.examples [ES]
Результат: output/<INSTR>_examples.png
Слева — контекст: дни от начала композита до 3 дней после касания
(серая линия — range дня, чёрная — VA дня, синий прямоугольник — VA композита,
оранжевая линия — уровень зоны, жёлтая полоса — день касания).
Справа — TPO-профиль дня касания с уровнем зоны.
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from scripts.stage4_events import build_all
from tpo.data import ROOT, load_config
from tpo.plot import draw_day

CASES = [
    ("Первое касание VAH → отказ", dict(level_type="VAH", touch_number=1, outcome="rejection")),
    ("Первое касание VAH → принятие", dict(level_type="VAH", touch_number=1, outcome="acceptance")),
    ("Касание VAL сверху → отказ", dict(level_type="VAL", approach="above", outcome="rejection")),
    ("POC → принятие и доход до противоположной границы",
     dict(level_type="POC", outcome="acceptance", reached_opposite_d0=True)),
    ("Гэп через уровень (VAH)", dict(level_type="VAH", gap_through=True)),
]


def pick(ev: pd.DataFrame, cond: dict, used: set) -> pd.Series:
    m = ev.copy()
    for k, v in cond.items():
        m = m[m[k] == v]
    # предпочитаем свежие композиты (зона моложе 30 дней), без других зон в этот день
    m = m[(m.zone_age_days <= 30) & ~m.date.isin(used)]
    pref = m[m.multi_zone <= 2]
    m = pref if len(pref) else m
    return m.sort_values("date").iloc[len(m) // 2]


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    bars, days, comps, ev, tick = build_all(instrument, cfg)
    ev = ev[~ev.breakout_day]
    full = days[days.day_type != "Short"].reset_index(drop=True)
    comp = comps.set_index("composite_id")

    fig, axes = plt.subplots(len(CASES), 2, figsize=(16, 5.2 * len(CASES)),
                             gridspec_kw={"width_ratios": [3, 1]})
    used: set = set()
    for (title, cond), (axc, axp) in zip(CASES, axes):
        e = pick(ev, cond, used)
        used.add(e.date)
        c = comp.loc[e.composite_id]
        i0 = max(full.index[full.date == c.start][0] - 2, 0)
        i_t = full.index[full.date == e.date][0]
        i1 = min(i_t + 3, len(full) - 1)
        d = full.iloc[i0:i1 + 1].reset_index(drop=True)
        x = {dt: i for i, dt in enumerate(d.date)}

        axc.vlines(range(len(d)), d.low, d.high, color="0.6", lw=1)
        axc.vlines(range(len(d)), d.val, d.vah, color="0.15", lw=3)
        cx0 = x[c.start] - 0.5
        cx1 = x.get(c.end, len(d) - 1) + 0.5
        axc.add_patch(Rectangle((cx0, c.val), cx1 - cx0, c.vah - c.val, color="tab:blue", alpha=0.22, lw=0))
        axc.add_patch(Rectangle((cx0, c.low), cx1 - cx0, c.high - c.low, fill=False, ec="tab:blue", lw=0.8))
        axc.hlines(e.level_price, cx0, len(d) - 0.5, color="tab:orange", lw=1.6, ls="--")
        axc.text(len(d) - 0.5, e.level_price, f" {e.level_type} {e.level_price:.2f}", color="tab:orange",
                 va="center", fontsize=8)
        axc.axvspan(x[e.date] - 0.5, x[e.date] + 0.5, color="gold", alpha=0.25)
        ticks = list(range(0, len(d), max(len(d) // 10, 1)))
        axc.set_xticks(ticks)
        axc.set_xticklabels([d.date[i].strftime("%m-%d") for i in ticks], fontsize=7)
        axc.tick_params(axis="y", labelsize=7)
        axc.set_xlim(-1, len(d) + 2.5)
        reach = "да" if e.reached_opposite_d0 else "нет"
        axc.set_title(
            f"{title}\nКомпозит #{e.composite_id}: {c.start:%Y-%m-%d} – {c.end:%Y-%m-%d} ({c.n_days} дн.), "
            f"касание {e.date:%Y-%m-%d}, №{e.touch_number}, подход {'сверху' if e.approach == 'above' else 'снизу'}"
            f"{', гэп' if e.gap_through else ''}",
            fontsize=9, loc="left")

        drow = full[full.date == e.date].iloc[0]
        draw_day(axp, bars[bars.date == e.date], drow, tick,
                 f"{e.date:%Y-%m-%d}: {drow.day_type}, форма {drow.profile_shape}")
        axp.axhline(e.level_price, color="tab:orange", lw=1.6, ls="--")
        lo = min(drow.low, e.level_price, e.opposite_boundary if e.reached_opposite_d0 else drow.low)
        hi = max(drow.high, e.level_price)
        axp.set_ylim(lo - 4 * tick, hi + 4 * tick)
        axp.text(0.02, 0.01,
                 f"Исход: {'принятие' if e.outcome == 'acceptance' else 'отказ'}"
                 f" (касание в периоде {'ABCDEFGHIJKLM'[int(e.first_touch_period)]})\n"
                 f"Противоп. граница {e.opposite_boundary:.2f} в тот же день: {reach}\n"
                 f"MFE {e.mfe_ticks} тиков, MAE {e.mae_ticks} тиков",
                 transform=axp.transAxes, fontsize=7, va="bottom",
                 bbox=dict(facecolor="white", alpha=0.85, edgecolor="0.8"))
    fig.suptitle(f"{instrument}: 5 примеров касания зон прошлых композитов", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    out = ROOT / "output" / f"{instrument}_examples.png"
    fig.savefig(out, dpi=110)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
