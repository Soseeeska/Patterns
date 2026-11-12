"""Примеры отторжений в диапазон / VA прошлого дня от зоны композита — на 30m-барах.

Окно: предыдущая сессия + сессия события (30m-бары из набора баров, по
умолчанию ES_ETH). Линии — High / Low / VAH / VAL / POC прошлого RTH-дня
(серым фоном — его RTH-часы), полоса — зона композита, от которой было
отторжение; стрелка — бар-событие; подпись — дошла ли цена до POC.

Запуск:  python -m scripts.prev_day_rejections_plot [ES] [ES_ETH] [YYYY-MM-DD:Уровень ...]
         (сначала python -m scripts.prev_day_rejections ES ES_ETH; берутся строгие события)
Результат: output/<TAG>_prev_day_rejection_examples.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted, load_test_bars, tag
from scripts.top_zones_plot import INK, MUTED, SURFACE, _clean, candles
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands

DEFAULT = ["2026-10-21:High", "2026-10-22:Low", "2026-11-05:VAL", "2026-11-09:High", "2026-11-10:VAH"]
ZONE = "#2a78d6"
LV = {"High": "#e34948", "VAH": "#eb6834", "POC": "#6250d6", "VAL": "#1baf7a", "Low": "#008300"}


def main(instrument: str = "ES", bars_instrument: str = "ES_ETH", *picks: str) -> None:
    picks = list(picks) or DEFAULT
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick).set_index("zone_id")
    tb = load_test_bars(bars_instrument)[0].sort_values("ts").reset_index(drop=True)
    name = tag(instrument, bars_instrument, None)
    ev = pd.read_csv(out / f"{name}_prev_day_rejections_strict.csv", parse_dates=["date"])
    ev["ts"] = pd.to_datetime(ev.ts, utc=True).dt.tz_convert(cfg["data"]["timezone"])
    sessions = np.sort(tb.date.unique())
    rth = daily[~daily.short_day].set_index("date")

    fig, axes = plt.subplots(len(picks), 1, figsize=(15, 4.6 * len(picks)), squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax, pk in zip(axes[:, 0], picks):
        d, lv = pk.split(":")
        e = ev[(ev.date == pd.Timestamp(d)) & (ev.level == lv)].iloc[0]
        z = bands.loc[e.comp_zone]
        j = np.searchsorted(sessions, np.datetime64(e.date))
        prev_s = sessions[j - 1]
        p = rth.loc[prev_s]
        w = tb[tb.date.isin([prev_s, sessions[j]])].reset_index(drop=True)
        x = np.arange(len(w))
        candles(ax, x, w.open.to_numpy(), w.high.to_numpy(), w.low.to_numpy(), w.close.to_numpy(), w=0.65)
        # RTH-часы прошлого дня
        tod = w.ts.dt.hour * 60 + w.ts.dt.minute
        rmask = (w.date == prev_s) & (tod >= 8 * 60 + 30) & (tod < 15 * 60)
        if rmask.any():
            ax.axvspan(x[rmask].min() - 0.5, x[rmask].max() + 0.5, color="#ecebe7", zorder=0)
            ax.text(x[rmask].min(), 1, " RTH прошлого дня", transform=ax.get_xaxis_transform(), va="top",
                    fontsize=7, color=MUTED)
        ax.axvline(np.flatnonzero(w.date == sessions[j])[0] - 0.5, color=MUTED, lw=0.8, ls=":")
        tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
        ax.axhspan(z.lo - tol, z.hi + tol, color=ZONE, alpha=0.25, lw=0, zorder=1)   # зона ± допуск
        ax.hlines([z.lo, z.hi], -1, len(w) + 7, color=ZONE, lw=0.8, zorder=1)
        for k, val in (("High", p.high), ("VAH", p.vah), ("POC", p.poc), ("VAL", p.val), ("Low", p.low)):
            ax.axhline(val, color=LV[k], lw=1.0 if k == lv else 0.6, ls="-" if k == lv else "--", zorder=1)
            ax.text(len(w) + 0.5, val, f"{k} {val:.2f}", fontsize=7, color=LV[k], va="center")
        i = int(np.flatnonzero(w.ts == e.ts)[0])
        up = e.side == "выше"
        y = w.high[i] if up else w.low[i]
        span = w.high.max() - w.low.min()
        ax.annotate("", (i, y), (i, y + (0.12 if up else -0.12) * span),
                    arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.4))
        lo_y = min(w.low.min(), z.lo, p.low) - 0.05 * span
        hi_y = max(w.high.max(), z.hi, p.high) + 0.15 * span
        ax.set_ylim(lo_y, hi_y)
        ax.set_xlim(-1, len(w) + 7)
        st = np.flatnonzero((w.ts.dt.hour.to_numpy() % 3 == 0) & (w.ts.dt.minute.to_numpy() == 0))
        ax.set_xticks(st, [f"{w.ts[k]:%d.%m %H:%M}" for k in st], fontsize=7)
        _clean(ax)
        res = "дошла до POC прошлого дня" if e.reached_poc else "до POC прошлого дня не дошла"
        ztype = {"HVN": "HVN-полоса", "POC": "POC", "VAH": "VAH", "VAL": "VAL"}[z.level_type]
        ax.set_title(f"Сессия {e.date:%d.%m.%Y}: хвост {'выше' if up else 'ниже'} {lv} прошлого дня "
                     f"({e.level_price:.2f}), кончик {e.extreme:.2f}, бар {e.ts:%d.%m %H:%M} CT  ·  "
                     f"зона композита #{z.composite_id}: {ztype} {z.lo:.2f}–{z.hi:.2f}  ·  {res}",
                     fontsize=9, loc="left", color=INK)
    fig.suptitle(f"{name}: отторжение обратно в диапазон / VA прошлого дня от зоны композита баланса "
                 f"(30m-бары ETH+RTH, время CT)", x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.005, "Синяя полоса — зона композита ± 2 тика, стрелка — бар-отторжение (open и close внутри, хвост за уровнем), "
             "пунктир — начало сессии события (17:00 CT). Цены back-adjusted (ESZ6).", fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.01, 1, 0.975))
    path = out / f"{name}_prev_day_rejection_examples.png"
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
