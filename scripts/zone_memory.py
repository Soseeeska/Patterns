"""Активные зоны по цене и их память: держат ли сегодня зоны, которые чаще всего отторгали цену.

Идея автора: смотреть не по времени, а по цене — брать зоны 1.8 в пределах ± среднего дневного диапазона от open,
по каждой считать, сколько раз цена её отторгала и принимала (tpo/zone_memory.py, всё до 08:30 CT дня), ранжировать
и ждать реакции от самых отторгаемых. Роль — по текущей стороне цены (красная под ценой — поддержка), не по цвету.
Серые — «смотреть, но не стоять стопом»: проверяется прокол за дальний край.

Событие — первый за день тест зоны из окна (tpo/zones18.first_tests): удержание, сделка от зоны, прокол. Фальш —
каждая зона сдвинута на ±U(1.5, 6) п. на всю жизнь (5 прогонов) со своей историей и своими тестами: показывает, даёт ли
память эффект именно на зоне или на любом уровне, от которого цена разворачивалась.

Запуск:  python -m scripts.zone_memory [build]      Вход: output/z18/snap_*.csv, output/z18/bars_raw.csv
        (bars_raw — python -m motivewave.parity.export_bars output/z18/bars_raw.csv --raw)
Результат: output/z18/memory_events.csv, отчёт в stdout.
"""
from __future__ import annotations

import sys
import zlib

import numpy as np
import pandas as pd

from scripts.zones18_mp import OUT
from tpo.data import load_bars, load_config
from tpo.zone_memory import history, load_series, ms
from tpo.zones18 import day_table, first_tests, load_snaps

N_PLAC = 5
COLOR = {"CORE": "синяя", "HVN_S": "зелёная", "HVN_R": "красная", "GREY": "серая"}


def build(cfg, tick):
    tz = cfg["data"]["timezone"]
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    days["rng_avg20"] = (days.high - days.low).where(~days.short_day).rolling(20, min_periods=10).mean().shift(1)
    series = load_series(OUT / "bars_raw.csv")
    snaps = load_snaps(OUT)
    by_day = {d: g.sort_values("period").reset_index(drop=True) for d, g in bars.groupby("date")}
    ok = days[(~days.short_day) & (days.y_short_day == False) & (~days.roll_day) & days.rng_avg20.notna()
              & days.date.isin(set(snaps.day))]
    ok = ok[[len(by_day[d]) == 13 for d in ok.date]].set_index("date", drop=False)
    # окно по цене: середина зоны в ± среднем дневном диапазоне от open
    s = snaps.merge(ok[["open", "rng_avg20"]].reset_index(), left_on="day", right_on="date")
    s = s[((s.lo + s.hi) / 2 - s.open).abs() <= s.rng_avg20].copy()
    s["cut"] = [ms(d + pd.Timedelta(hours=8, minutes=30), tz) for d in s.day]
    s["av"] = pd.to_datetime(s.av)
    rows = []
    for run in range(-1, N_PLAC):
        parts = []
        for (lo, hi, av), g in s.groupby(["lo", "hi", "av"]):
            if run >= 0:
                rng = np.random.default_rng(zlib.crc32(f"{lo}|{hi}|{av}|{run}".encode()))
                sh = np.round(rng.uniform(1.5, 6.0) * rng.choice([-1, 1]) / tick) * tick
            else:
                sh = 0.0
            start = ms(av - pd.Timedelta(days=1) + pd.Timedelta(hours=17), tz)
            h = history(series, lo + sh, hi + sh, start, g.cut.to_numpy())
            gg = g.copy()
            gg["lo"], gg["hi"] = lo + sh, hi + sh
            for k, v in h.items():
                gg[k] = v
            parts.append(gg)
        z = pd.concat(parts)
        for d, zd in z.groupby("day"):
            zd = zd.reset_index(drop=True)
            zd["rej"] = zd.rej_sup + zd.rej_res
            zd["touch"] = zd.touch_sup + zd.touch_res
            zd["rank_rej"] = zd.rej.rank(ascending=False, method="min")
            zd["rank_net"] = (zd.srej_sup + zd.srej_res - zd.acc_sup - zd.acc_res).rank(ascending=False, method="min")
            day = ok.loc[d]
            for e in first_tests(day, by_day[d], zd, tick):
                zr = zd.loc[e["zi"]]
                e.update(run=run, kind=COLOR[zr.kind], age=zr.age, rej_sup=zr.rej_sup, rej_res=zr.rej_res,
                         touch_sup=zr.touch_sup, touch_res=zr.touch_res, rej=zr.rej, touch=zr.touch,
                         rank_rej=zr.rank_rej, n_window=len(zd), **{c: zr[c] for c in
                         ("visit_sup", "srej_sup", "acc_sup", "visit_res", "srej_res", "acc_res", "rank_net")})
                rows.append(e)
        print(f"  прогон {'реальные' if run < 0 else run}: событий {sum(1 for r in rows if r['run'] == run)}")
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT / "memory_events.csv", index=False)
    return ev


def features(ev, split):
    ev["sample"] = np.where(ev.date < split, "до 2025", "2025+")
    ev["hold"] = (ev.outcome == "hold").astype(float)
    sup = ev.role == "поддержка"
    # память с той стороны, с которой зону тестируют сейчас
    ev["rej_side"] = np.where(sup, ev.rej_sup, ev.rej_res)
    ev["touch_side"] = np.where(sup, ev.touch_sup, ev.touch_res)
    ev["acc_side"] = ev.touch_side - ev.rej_side
    ev["mem_side"] = pd.cut(ev.rej_side, [-1, 0, 2, 5, 1e9], labels=["0", "1–2", "3–5", "6+"]).astype(str)
    ev.loc[ev.touch_side == 0, "mem_side"] = "не тестировалась"
    share = ev.rej / ev.touch.where(ev.touch >= 3)
    ev["share"] = pd.cut(share, [-0.01, 0.4, 0.6, 1.01], labels=["< 40% отторжений", "40–60%", "> 60%"]).astype(str)
    ev.loc[ev.touch < 3, "share"] = "< 3 касаний"
    ev["top"] = np.where(ev.rank_rej <= 3, "топ-3 по отторжениям в окне", "остальные")
    ev["kind_role"] = ev.kind + " как " + ev.role
    # сильная память: визиты с текущей стороны
    v = np.where(sup, ev.visit_sup, ev.visit_res)
    sr = np.where(sup, ev.srej_sup, ev.srej_res)
    ac = np.where(sup, ev.acc_sup, ev.acc_res)
    ev["visits"] = pd.cut(v, [-1, 0, 2, 5, 1e9], labels=["0 визитов", "1–2", "3–5", "6+"]).astype(str)
    net = sr - ac
    ev["net_side"] = np.select([v == 0, net >= 2, net <= -2], ["не было визитов", "отторгала (≥ +2)", "принимала (≤ −2)"],
                               "поровну (±1)")
    ev["strong_share"] = np.select([v < 3, sr / np.maximum(v, 1) >= 0.5, sr / np.maximum(v, 1) <= 0.2],
                                   ["< 3 визитов", "≥ 50% визитов — сильное отторжение", "≤ 20%"], "20–50%")
    ev["top_net"] = np.where(ev.rank_net <= 3, "топ-3 в окне (отторжения − принятия)", "остальные")
    ev["pierce1"] = (ev.pierce >= 1.0).astype(float)
    ev["pierce_hold"] = ((ev.pierce >= 1.0) & (ev.outcome == "hold")).astype(float)
    return ev


def show(ev, by, metrics=("hold", "r"), min_n=60, title=""):
    print(f"\n### {title}")
    hdr = " | ".join(f"{m:>8s}: до 2025 · 2025+ (фальш)" for m in metrics)
    print(f"  {'':34s} {hdr}")
    real, plac = ev[ev.run < 0], ev[ev.run >= 0]
    for key in real[by].dropna().unique():
        cells = []
        ns = []
        for m in metrics:
            c = []
            for smp in ("до 2025", "2025+"):
                a = real[(real[by] == key) & (real["sample"] == smp)][m]
                pr = plac[(plac[by] == key) & (plac["sample"] == smp)].groupby("run")[m].mean()
                fmt = (lambda v: f"{v:+.2f}") if m == "r" else (lambda v: f"{v:4.0%}")
                c.append("   —  " if len(a) < min_n else f"{fmt(a.mean())} ({fmt(pr.min())}…{fmt(pr.max())})")
                ns.append(len(a))
            cells.append(" · ".join(c))
        print(f"  {str(key):34s} " + " | ".join(cells) + f"   n={ns[0]}/{ns[1]}")


def main(*args):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    if "build" in args or not (OUT / "memory_events.csv").exists():
        ev = build(cfg, tick)
    else:
        ev = pd.read_csv(OUT / "memory_events.csv", parse_dates=["date"])
    ev = features(ev, split)
    r = ev[ev.run < 0]
    print(f"реальных тестов зон из окна ± средний диапазон: {len(r)} за {r.date.nunique()} дней; "
          f"зон в окне в среднем {r.groupby('date').n_window.first().mean():.0f}")
    print("ячейка: реальные (разброс 5 фальш-прогонов, зоны сдвинуты на 1.5–6 п. со своей историей)")
    show(ev, "mem_side", title="Отторжения с той стороны, с которой тестируют сейчас (поддержка — сверху, сопротивление — снизу)")
    show(ev, "share", title="Доля отторжений за всю жизнь зоны (от 3 касаний)")
    show(ev, "top", title="Самые отторгаемые в окне против остальных")
    show(ev, "visits", title="Сильная память: визитов с текущей стороны (визит — касание после 2 ч без касаний)")
    show(ev, "net_side", title="Сильные отторжения − принятия с текущей стороны (за всю жизнь зоны)")
    show(ev, "strong_share", title="Доля визитов с сильным отторжением (ушли на 8 п., не закрывшись за зоной)")
    show(ev, "top_net", title="Топ-3 в окне по «сильные отторжения − принятия» против остальных")
    show(ev, "kind_role", title="Цвет × текущая роль (по стороне цены)")
    show(ev, "kind", metrics=("hold", "pierce1", "pierce_hold"),
         title="Серые и прокол: pierce1 — цена зашла за дальний край ≥ 1 п.; pierce_hold — зашла и всё равно удержалась")


if __name__ == "__main__":
    main(*sys.argv[1:])
