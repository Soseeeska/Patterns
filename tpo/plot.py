"""Отрисовка TPO-профиля дня: клетка = TPO, цвет = период (A тёмный → M жёлтый)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

CMAP = plt.get_cmap("viridis", 13)


def draw_day(ax, g, d, tick: float, title: str = "", periods: int = 13) -> None:
    """g — бары одного дня, d — строка дневной таблицы; periods — число
    периодов сессии (13 для RTH, 46 для ETH) — для цвета и ширины."""
    cmap = plt.get_cmap("viridis", periods)
    filled: dict[int, int] = {}
    xs, ys, cs = [], [], []
    for _, r in g.sort_values("period").iterrows():
        for t in range(int(round(r.low / tick)), int(round(r.high / tick)) + 1):
            x = filled.get(t, 0)
            filled[t] = x + 1
            xs.append(x)
            ys.append(t * tick)
            cs.append(int(r.period))
    ax.barh(ys, 1, left=xs, height=tick, color=cmap(cs), edgecolor="none", align="center")
    for lvl, color, name in [(d.vah, "tab:blue", "VAH"), (d.val, "tab:blue", "VAL"),
                             (d.poc, "tab:red", "POC")]:
        ax.axhline(lvl, color=color, lw=0.8, alpha=0.7)
        ax.text(1, lvl, f"{name} {lvl:.2f} ", color=color, fontsize=6,
                va="bottom", ha="right", transform=ax.get_yaxis_transform())
    ax.plot([-0.6, -0.6], [d.ib_low, d.ib_high], color="k", lw=2.5)
    ax.plot([-0.6], [d.close], marker="<", color="tab:red", ms=5)
    ax.set_ylim(d.low - 3 * tick, d.high + 3 * tick)
    ax.set_xlim(-1, max(13, max(filled.values()) + 2))
    ax.set_xticks([])
    ax.tick_params(labelsize=6)
    ax.set_title(title, fontsize=8)


def draw_days(bars, daily, dates, tick, path, titles=None, ncols=5, header=""):
    n = len(dates)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.4 * ncols, 6.2 * nrows), squeeze=False)
    by_date = daily.set_index("date")
    for k, ax in enumerate(axes.flat):
        if k >= n:
            ax.axis("off")
            continue
        date = dates[k]
        title = titles[k] if titles else f"{date:%Y-%m-%d}"
        draw_day(ax, bars[bars["date"] == date], by_date.loc[date], tick, title)
    fig.suptitle((header + "\n" if header else "") +
                 "TPO по периодам: A (тёмно-фиолетовый) → M (жёлтый); чёрная черта — IB, "
                 "красный треугольник — close", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def bars_on_days(ax, bars, dates, periods: int, color="#0b0b0b", lw=0.35, alpha=0.55) -> None:
    """30m-бары поверх оси, где x — номер дня в dates: бар периода p дня i
    рисуется отрезком low–high на x = i − 0.5 + (p + 0.5) / periods."""
    pos = {d: i for i, d in enumerate(dates)}
    b = bars[bars["date"].isin(pos)]
    x = b["date"].map(pos).to_numpy() - 0.5 + (b["period"].to_numpy() + 0.5) / periods
    ax.vlines(x, b["low"].to_numpy(), b["high"].to_numpy(), color=color, lw=lw, alpha=alpha, zorder=2)
