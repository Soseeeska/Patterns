"""Как выглядит подгонка границ зон по краям тел: окно подгонки и следующие сессии.

По каждой зоне — 30m-свечи (ETH+RTH): последние 8 сессий подгонки и первые
8 сессий проверки; доли в заголовке — по полным окнам (по calib_sessions).
Без id берутся 4 сдвинутые зоны последних двух лет с наибольшим числом
касаний и в подгонке, и в проверке. Пунктир — исходные границы, сплошные — подогнанные;
точки — бары, у которых край тела на подогнанной границе (± 1 тик).

Запуск:  python -m scripts.zone_align_plot [ES] [ES_ETH] [zone_id ...]
         (сначала python -m scripts.zone_align_test)
Результат: output/<TAG>_zone_align_examples.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_test_bars, tag
from scripts.top_zones_plot import INK, MUTED, SURFACE, _clean, candles
from tpo.data import ROOT, load_config

ZONE, EDGE = "#2a78d6", "#eb6834"


def main(instrument: str = "ES", bars_instrument: str = "ES_ETH", *ids: str) -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    tick = cfg["data"]["instruments"][instrument]["tick_size"]
    n_cal = cfg["zone_align"]["calib_sessions"]
    name = tag(instrument, bars_instrument, None)
    a = pd.read_csv(out / f"{name}_zone_align.csv", parse_dates=["available_from"])
    moved = a[(a.lo != a.lo_orig) | (a.hi != a.hi_orig)]
    b = load_test_bars(bars_instrument)[0].sort_values("ts").reset_index(drop=True)
    sessions = np.sort(b.date.unique())
    top_all, bot_all = np.maximum(b.open, b.close).to_numpy(), np.minimum(b.open, b.close).to_numpy()

    def window_stats(r, d0, d1):
        """Бары-касания и доля с краем тела на подогнанной границе за сессии [d0, d1)."""
        m = ((b.date >= d0) & (b.date < d1)).to_numpy()
        t = m & (b.low.to_numpy() <= r.hi) & (b.high.to_numpy() >= r.lo)
        e = np.zeros(len(b), bool)
        for edge in (r.lo, r.hi):
            e |= (np.abs(top_all - edge) <= tick + 1e-9) | (np.abs(bot_all - edge) <= tick + 1e-9)
        return int(t.sum()), (t & e).sum() / max(t.sum(), 1)

    def windows(r):
        i = int(np.searchsorted(sessions, np.datetime64(r.available_from)))
        return sessions[i - n_cal], sessions[i], sessions[min(i + n_cal, len(sessions) - 1)]

    if ids:
        sel = moved[moved.zone_id.isin([int(i) for i in ids])]
    else:   # больше всего касаний и в подгонке, и в проверке (последние 2 года)
        cand = moved[moved.available_from >= sessions[-1] - np.timedelta64(730, "D")].copy()
        cand["n"] = [min(window_stats(r, *windows(r)[:2])[0], window_stats(r, *windows(r)[1:])[0])
                     for r in cand.itertuples()]
        sel = cand.sort_values("n", ascending=False).head(4).sort_values("available_from")
    SHOW = 8      # сессий на рисунке с каждой стороны от начала проверки

    fig, axes = plt.subplots(len(sel), 1, figsize=(15, 4.4 * len(sel)), squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax, r in zip(axes[:, 0], sel.itertuples()):
        c0, t0, t1 = windows(r)
        n_c, sh_c = window_stats(r, c0, t0)
        n_t, sh_t = window_stats(r, t0, t1)
        i_test = int(np.searchsorted(sessions, np.datetime64(r.available_from)))
        s0, s1 = sessions[i_test - SHOW], sessions[min(i_test + SHOW, len(sessions) - 1)]
        w = b[(b.date >= s0) & (b.date < s1)].reset_index(drop=True)
        x = np.arange(len(w))
        candles(ax, x, w.open.to_numpy(), w.high.to_numpy(), w.low.to_numpy(), w.close.to_numpy(), w=0.6)
        xt = int(np.flatnonzero(w.date == r.available_from)[0])
        ax.axvspan(-0.5, xt - 0.5, color="#f1f0ec", zorder=0)
        ax.axvline(xt - 0.5, color=INK, lw=0.8, ls=":")
        ax.axhspan(r.lo, r.hi, color=ZONE, alpha=0.18, lw=0, zorder=1)
        ax.hlines([r.lo, r.hi], -0.5, len(w) - 0.5, color=ZONE, lw=1.2, zorder=2)
        ax.hlines([r.lo_orig, r.hi_orig], -0.5, len(w) - 0.5, color=MUTED, lw=1.0, linestyles="--", zorder=2)
        top, bot = np.maximum(w.open, w.close).to_numpy(), np.minimum(w.open, w.close).to_numpy()
        for edge in (r.lo, r.hi):
            m = ((np.abs(top - edge) <= tick + 1e-9) | (np.abs(bot - edge) <= tick + 1e-9))
            ax.scatter(x[m], np.full(m.sum(), edge), s=14, color=EDGE, zorder=4, linewidths=0)
        pad = max(10, 2 * (r.hi - r.lo))
        ax.set_ylim(min(r.lo, r.lo_orig) - 3 * pad, max(r.hi, r.hi_orig) + 3 * pad)
        ax.set_xlim(-1, len(w))
        st = np.flatnonzero(np.r_[True, w.date.to_numpy()[1:] != w.date.to_numpy()[:-1]])[::4]
        ax.set_xticks(st, [f"{w.date[k]:%d.%m}" for k in st])
        _clean(ax)
        ax.text(xt / 2, 1.0, f"подгонка (последние {SHOW} из {n_cal} сессий)", transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=8, color=MUTED)
        ax.text((xt + len(w)) / 2, 1.0, f"проверка (первые {SHOW} сессий)", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8, color=MUTED)
        ax.set_title(f"Зона #{r.zone_id}: была {r.lo_orig:.2f}–{r.hi_orig:.2f}, стала {r.lo:.2f}–{r.hi:.2f}  ·  "
                     f"край тела на подогнанной границе: в подгонке {sh_c:.0%} из {n_c} баров-касаний, "
                     f"в следующие {n_cal} сессий — {sh_t:.0%} из {n_t}",
                     fontsize=9.5, loc="left", color=INK)
    fig.suptitle(f"{name}: подгонка границ зон по краям тел 30m-свечей", x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.004, "Пунктир — исходные границы, синие линии — подогнанные. Оранжевые точки — край тела бара "
             "на подогнанной границе (± 1 тик). Серый фон — сессии подгонки. Цены back-adjusted (ESZ6).",
             fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.012, 1, 0.98))
    path = out / f"{name}_zone_align_examples.png"
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
