"""Примеры отторжений в диапазон / VA прошлого дня от зоны композита — RTH TPO-принтами.

Слева профиль прошлого RTH-дня, справа — дня события, буквы A–M = периоды
08:30–14:30 CT. Линии — High / VAH / POC / VAL / Low прошлого дня, синяя
полоса — зона композита ± допуск; буквы периода-отторжения в дне события —
красные. Строка профиля укрупнена (row_pts), чтобы буквы читались.

Запуск:  python -m scripts.prev_day_rejections_tpo [ES] [YYYY-MM-DD:Уровень ...]
         (сначала python -m scripts.prev_day_rejections ES; берутся строгие события)
Результат: output/ES_prev_day_rejection_tpo_<N>.png — по картинке на пример
"""
from __future__ import annotations

import math
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.prev_day_rejections_plot import LV, ZONE
from scripts.stage1_profiles import load_adjusted
from scripts.top_zones_plot import INK, MUTED, SURFACE
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands

DEFAULT = ["2026-07-27:Low", "2026-10-15:VAL", "2026-10-27:High", "2026-10-28:High", "2026-10-29:Low"]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
HL = "#e34948"
FS = 9.5


def draw_prints(ax, g: pd.DataFrame, x0: float, R: float, hl_period: int | None = None) -> int:
    """TPO-буквы дня: каждая строка R пунктов, буквы встают слева направо по
    порядку периодов. Возвращает ширину профиля в колонках."""
    filled: dict[int, int] = {}
    for r in g.sort_values("period").itertuples():
        for k in range(math.floor(r.low / R), math.floor(r.high / R) + 1):
            col = filled.get(k, 0)
            filled[k] = col + 1
            hot = hl_period is not None and r.period == hl_period
            ax.text(x0 + col, (k + 0.5) * R, LETTERS[r.period], ha="center", va="center",
                    fontsize=FS, family="monospace", color=HL if hot else INK,
                    fontweight="bold" if hot else "normal", zorder=4)
    return max(filled.values())


def main(instrument: str = "ES", *picks: str) -> None:
    picks = list(picks) or DEFAULT
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick).set_index("zone_id")
    ev = pd.read_csv(out / f"{instrument}_prev_day_rejections_strict.csv", parse_dates=["date"])
    ev["ts"] = pd.to_datetime(ev.ts, utc=True).dt.tz_convert(cfg["data"]["timezone"])
    rth = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick

    paths = []
    for n_pick, pk in enumerate(picks, 1):
        d, lv = pk.split(":")
        e = ev[(ev.date == pd.Timestamp(d)) & (ev.level == lv)].iloc[0]
        z = bands.loc[e.comp_zone]
        k = int(np.flatnonzero(rth.date == e.date)[0])
        p, cur = rth.iloc[k - 1], rth.iloc[k]
        gp, gc = bars[bars.date == p.date], bars[bars.date == cur.date]
        lo = min(p.low, cur.low, z.lo) - 3
        hi = max(p.high, cur.high, z.hi) + 3
        R = max(1.0, math.ceil((hi - lo) / 60 / 0.5) * 0.5)          # укрупнённая строка, пункты
        period = int(bars.loc[bars.ts == e.ts, "period"].iloc[0])
        n_rows = (hi - lo) / R
        fig, ax = plt.subplots(figsize=(13, max(7.0, n_rows * 0.19)))
        fig.patch.set_facecolor(SURFACE)

        w1 = draw_prints(ax, gp, 0, R)
        x2 = w1 + 4
        w2 = draw_prints(ax, gc, x2, R, period)
        right = x2 + w2 + 1
        ax.axhspan(z.lo - tol, z.hi + tol, color=ZONE, alpha=0.22, lw=0, zorder=1)
        ax.hlines([z.lo, z.hi], -1, right, color=ZONE, lw=0.8, zorder=1)
        levels = (("High", p.high), ("VAH", p.vah), ("POC", p.poc), ("VAL", p.val), ("Low", p.low))
        for name, val in levels:
            ax.hlines(val, -1, right, color=LV[name], lw=1.6 if name == lv else 0.9,
                      linestyles="-" if name == lv else "--", zorder=2)
        # подписи справа: одинаковые цены — в одну строку, близкие — раздвинуты
        labels = {}
        for name, val in levels:
            labels.setdefault(round(val, 2), []).append(name)
        ys, prev = [], None
        for val in sorted(labels, reverse=True):
            y = val if prev is None else min(val, prev - 1.6 * R)
            ys.append((val, y))
            prev = y
        for val, y in ys:
            names = labels[val]
            ax.annotate(f"{' / '.join(names)} {val:.2f}", (right, val), xytext=(right + 0.6, y),
                        fontsize=9, color=LV[names[0]], va="center", annotation_clip=False,
                        arrowprops=dict(arrowstyle="-", color=LV[names[0]], lw=0.6))
        ax.text(w1 / 2, hi + R * 0.3, f"{p.date:%d.%m} — прошлый день", ha="center", fontsize=10, color=MUTED)
        ax.text(x2 + w2 / 2, hi + R * 0.3, f"{cur.date:%d.%m} — день события", ha="center", fontsize=10,
                color=INK)
        ax.set_xlim(-1.5, right + 10)
        ax.set_ylim(lo, hi + R * 2)
        ax.set_xticks([])
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color="#ecebe7", lw=0.5)
        ax.tick_params(axis="y", labelsize=8.5, colors=MUTED)
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color("#c9c8c2")
        res = "дошла до POC прошлого дня" if e.reached_poc else "до POC прошлого дня не дошла"
        ztype = {"HVN": "HVN-полоса", "POC": "POC", "VAH": "VAH", "VAL": "VAL"}[z.level_type]
        ax.set_title(f"{cur.date:%d.%m.%Y}, период {LETTERS[period]} ({e.ts:%H:%M} CT): хвост "
                     f"{e.side} {lv} прошлого дня ({e.level_price:.2f}) до {e.extreme:.2f}, close обратно внутри\n"
                     f"Зона композита #{z.composite_id} ({ztype} {z.lo:.2f}–{z.hi:.2f}) — синяя полоса  ·  "
                     f"{res}  ·  строка профиля {R:g} п.", fontsize=10.5, loc="left", color=INK)
        fig.text(0.01, 0.005, "RTH TPO: A = 08:30 … M = 14:30 CT. Красные буквы — период-отторжение. "
                 "Сплошная линия — уровень, за который вышел хвост. Цены back-adjusted (ESZ6).",
                 fontsize=8, color=MUTED)
        fig.tight_layout(rect=(0, 0.015, 1, 1))
        path = out / f"{instrument}_prev_day_rejection_tpo_{n_pick}.png"
        fig.savefig(path, dpi=130, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)
    print("\n".join(map(str, paths)))


if __name__ == "__main__":
    main(*sys.argv[1:])
