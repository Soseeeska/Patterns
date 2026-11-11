"""5 композитов подряд на одной картинке.

Цена — общая шкала в реальном масштабе. Каждый композит в своём слоте по
горизонтали; ширина строки = число TPO (1 TPO — одна и та же ширина для всех),
так что форма и высота профилей сравнимы. Цвет внутри профиля — день композита
(светлый — первый, тёмный — последний). Между слотами — дни вне композитов
(серая линия — range дня, чёрная — VA дня, оранжевая засечка — close).

Запуск:  python -m scripts.composites_view [ES] [первый composite_id] [сколько]
Результат: output/<INSTR>_composites_view.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config

REASON = {"close_above": "закрылся вверх ↑", "close_below": "закрылся вниз ↓", "open": "формируется"}


def main(instrument: str = "ES", first: str = "167", count: str = "5") -> None:
    cfg = load_config()
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    sel = comps[(comps.composite_id >= int(first))].head(int(count))
    full = daily[~daily.short_day].sort_values("date").reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(22, 11))
    x = 0.0
    gap_w = 3.0          # ширина одного дня вне композитов
    pad = 4.0
    prev_end = None
    lo_all, hi_all = np.inf, -np.inf
    for c in sel.itertuples():
        # дни между композитами
        if prev_end is not None:
            between = full[(full.date > prev_end) & (full.date < c.start)]
            for d in between.itertuples():
                cx = x + gap_w / 2
                ax.vlines(cx, d.low, d.high, color="0.6", lw=1)
                ax.vlines(cx, d.val, d.vah, color="0.15", lw=3)
                ax.hlines(d.close, cx, cx + gap_w * 0.4, color="tab:orange", lw=1.5)
                lo_all, hi_all = min(lo_all, d.low), max(hi_all, d.high)
                ax.text(cx, d.low - 2, f"{d.date:%m-%d}", rotation=90, ha="center", va="top", fontsize=7,
                        color="0.4")
                x += gap_w
            x += pad / 2
        days = full[(full.date >= c.start) & (full.date <= c.end)].date.tolist()
        lo_t = int(round(c.low / tick))
        hi_t = int(round(c.high / tick))
        nrows = hi_t - lo_t + 1
        acc = np.zeros(nrows)
        cmap = plt.get_cmap("YlGnBu")
        for k, d in enumerate(days):
            p = profiles[d]
            cnt = np.zeros(nrows)
            a = p.low - lo_t
            cnt[a:a + len(p.counts)] = p.counts
            color = cmap(0.25 + 0.7 * k / max(len(days) - 1, 1))
            m = cnt > 0
            ys = (lo_t + np.flatnonzero(m)) * tick
            ax.barh(ys, cnt[m], left=x + acc[m], height=tick, color=color, edgecolor="none")
            acc += cnt
        w = acc.max()
        ax.hlines([c.vah, c.val], x, x + w, color="tab:blue", lw=1.2)
        ax.hlines(c.poc, x, x + w, color="tab:red", lw=1.4)
        ax.text(x, c.high + 3, f"#{c.composite_id}  {c.start:%Y-%m-%d} – {c.end:%m-%d}\n"
                f"{c.n_days} дн., {REASON.get(c.end_reason, c.end_reason)}",
                fontsize=9, va="bottom")
        ax.text(x + w + 0.5, c.vah, f"VAH {c.vah:.2f}", fontsize=7, color="tab:blue", va="center")
        ax.text(x + w + 0.5, c.val, f"VAL {c.val:.2f}", fontsize=7, color="tab:blue", va="center")
        ax.text(x + w + 0.5, c.poc, f"POC {c.poc:.2f}", fontsize=7, color="tab:red", va="center")
        lo_all, hi_all = min(lo_all, c.low), max(hi_all, c.high)
        x += w + pad + 10
        prev_end = c.end

    ax.set_xlim(-2, x)
    ax.set_ylim(lo_all - 25, hi_all + 25)
    ax.set_xticks([])
    ax.set_ylabel("Цена ES (back-adjusted)")
    ax.grid(axis="y", color="0.92")
    ax.set_title(f"{instrument}: {len(sel)} композитов подряд. Цена в реальном масштабе, ширина строки = число TPO "
                 "(одинаковая единица для всех). Цвет — день композита (светлый — первый, тёмный — последний). "
                 "Синие — VAH/VAL, красная — POC. Между профилями — дни вне композитов (оранжевая засечка — close).", fontsize=10, loc="left",
                 wrap=True)
    fig.tight_layout()
    out = ROOT / "output" / f"{instrument}_composites_view.png"
    fig.savefig(out, dpi=120)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
