"""Зоны TPO Zones 1.8 × классический рыночный профиль: где внутри дня зона даёт реакцию.

Вход: снимки зон `output/z18/snap_*.csv` (ZoneSnap: зоны на каждый день к открытию RTH, без заглядывания вперёд):
    python -m motivewave.parity.export_bars output/z18/bars_raw.csv --raw
    javac -encoding UTF-8 -d /tmp/cls motivewave/src/patterns/zones/ZoneEngine.java \
        motivewave/src/patterns/zones/ZonePlan.java motivewave/parity/*.java
    java -cp /tmp/cls patterns.zones.ZoneSnap output/z18/bars_raw.csv output/z18/snap_1.csv 2022-01-01 2026-12-31
    (≈ 1 с на день; можно кусками по датам параллельно — snap_*.csv читаются все)
События и правила — `tpo/zones18.py`. Фальш: вся карта дня сдвинута на ±U(1.5, 6) п. (20 прогонов) — те же зоны с теми
же признаками на других ценах. Поиск — до split_date, проверка — после.

Запуск:  python -m scripts.zones18_mp [build]      (build — пересчитать события, иначе читаются из output/z18)
Результат: output/z18/events.csv (реальные), output/z18/events_placebo.csv, отчёт в stdout.
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from tpo.data import ROOT, load_bars, load_config
from tpo.zones18 import day_table, first_tests, load_snaps

OUT = ROOT / "output" / "z18"
N_PLACEBO = 20
SHIFT = (1.5, 6.0)
ZCOLS = ["kind", "role_color", "va_major", "grey_cause", "comp_primary", "comp_live", "age", "rej", "av", "src"]

_G = {}


def _init(days, by_day, snaps, tick):
    _G.update(days=days, by_day=by_day, snaps=snaps, tick=tick)


def _one_day(i):
    day = _G["days"].loc[i]
    g = _G["by_day"][day.date]
    z = _G["snaps"][day.date]
    tick = _G["tick"]
    real = first_tests(day, g, z, tick)
    for r in real:
        r["run"] = -1
    rng = np.random.default_rng(int(day.date.strftime("%Y%m%d")))
    plac = []
    for k in range(N_PLACEBO):
        sh = np.round(rng.uniform(*SHIFT) * rng.choice([-1, 1]) / tick) * tick
        zz = z.copy()
        zz["lo"] += sh
        zz["hi"] += sh
        for r in first_tests(day, g, zz, tick):
            r["run"] = k
            plac.append(r)
    return real, plac


def build():
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    snaps = load_snaps(OUT)
    snap_by = {d: g.reset_index(drop=True) for d, g in snaps.groupby("day")}
    by_day = {d: g.sort_values("period").reset_index(drop=True) for d, g in bars.groupby("date")}
    ok = days[(~days.short_day) & (days.y_short_day == False) & (~days.roll_day) & days.ib_avg20.notna()
              & days.date.isin(snap_by.keys())]
    ok = ok[[len(by_day[d]) == 13 for d in ok.date]]
    print(f"дней со снимком и полным контекстом: {len(ok)} ({ok.date.min():%Y-%m-%d} … {ok.date.max():%Y-%m-%d})")
    with Pool(4, initializer=_init, initargs=(days, by_day, snap_by, tick)) as pool:
        res = pool.map(_one_day, list(ok.index), chunksize=8)
    real = pd.DataFrame([r for a, _ in res for r in a])
    plac = pd.DataFrame([r for _, b in res for r in b])
    for df in (real, plac):
        attrs = pd.concat([snap_by[d].loc[df.zi[df.date == d], ZCOLS].set_index(df.index[df.date == d])
                           for d in df.date.unique()])
        df[ZCOLS] = attrs[ZCOLS]
    real.to_csv(OUT / "events.csv", index=False)
    plac.to_csv(OUT / "events_placebo.csv", index=False)
    return real, plac


def enrich(df, split):
    df["sample"] = np.where(df.date < split, "до 2025", "2025+")
    df["hold"] = (df.outcome == "hold").astype(float)
    df["brk"] = (df.outcome == "break").astype(float)
    # роль зоны (цвет) против того, как её тестируют
    m = {("зелёная", "поддержка"): "по цвету", ("красная", "сопротивление"): "по цвету",
         ("зелёная", "сопротивление"): "против цвета", ("красная", "поддержка"): "против цвета"}
    df["color_fit"] = [m.get((c, r), c) for c, r in zip(df.role_color, df.role)]
    # кто выигрывает — за сторону зоны или против (поддержка и покупатель — «за»)
    sgn = np.where(df.role == "поддержка", 1, -1)
    df["with_ctrl"] = np.select([df.control * sgn > 0, df.control * sgn < 0], ["за", "против"], "поровну")
    # Далтон: ответная (покупка под value / продажа над value) или инициативная активность у зоны
    resp = ((df.role == "поддержка") & (df.zone_vs_yva == "под VA")) | ((df.role == "сопротивление") & (df.zone_vs_yva == "над VA"))
    init = ((df.role == "поддержка") & (df.zone_vs_yva == "над VA")) | ((df.role == "сопротивление") & (df.zone_vs_yva == "под VA"))
    df["activity"] = np.select([resp, init], ["ответная", "инициативная"], "в VA")
    df["confl_any"] = np.where(df.confl == "нет", "нет", "у уровня вчера")
    df["main"] = df.va_major.map({0: "—", 1: "главная поддержка", 2: "главное сопротивление"})
    df["fresh"] = np.where(df.age <= 1, "вчерашняя", "старше")
    return df


def table(real, plac, by, metric="hold", min_n=60):
    """Метрика по срезу: реальные зоны против распределения фальш-прогонов (среднее и перцентиль реального)."""
    rows = []
    keys = by if isinstance(by, list) else [by]
    for smp in ("до 2025", "2025+"):
        r = real[real["sample"] == smp]
        p = plac[plac["sample"] == smp]
        gr = r.groupby(keys)[metric].agg(["mean", "size"])
        gp = p.groupby(keys + ["run"])[metric].mean().unstack("run")
        for key, row in gr.iterrows():
            if row["size"] < min_n:
                continue
            pv = gp.loc[key].dropna().to_numpy() if key in gp.index else np.array([])
            pct = (pv < row["mean"]).mean() * 100 if len(pv) else np.nan
            rows.append({"срез": key if isinstance(key, str) else " · ".join(map(str, key)), "выборка": smp,
                         "n": int(row["size"]), metric: row["mean"], "фальш": pv.mean() if len(pv) else np.nan,
                         "перцентиль": pct})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    return t.pivot_table(index="срез", columns="выборка", values=["n", metric, "фальш", "перцентиль"], aggfunc="first")


def show(title, t, metric):
    print(f"\n### {title}")
    if t.empty:
        print("  (мало событий)")
        return
    for key, r in t.iterrows():
        cells = []
        for smp in ("до 2025", "2025+"):
            if (metric, smp) not in r or pd.isna(r[(metric, smp)]):
                cells.append(f"{'—':>34s}")
                continue
            v, f, pc, n = r[(metric, smp)], r[("фальш", smp)], r[("перцентиль", smp)], int(r[("n", smp)])
            fmt = (lambda x: f"{x:+.2f}R") if metric == "r" else (lambda x: f"{x:5.1%}")
            mark = "↑" if pc >= 95 else "↓" if pc <= 5 else " "
            cells.append(f"{fmt(v)} vs {fmt(f)} p{pc:3.0f}{mark} n={n:<5d}")
        print(f"  {str(key):46s} | " + " | ".join(cells))


def report(real, plac):
    print(f"\nсобытий (первый тест зоны за день): реальных {len(real)}, фальш {len(plac)} ({N_PLACEBO} прогонов)")
    print("ячейка: реальные vs среднее фальш, перцентиль реального среди фальш-прогонов (↑ ≥ 95, ↓ ≤ 5), n реальных")
    print("колонки: до 2025 | 2025+")
    real["all"] = "все тесты"
    plac["all"] = "все тесты"
    for metric, name in (("hold", "удержание"), ("r", "сделка от зоны, средний R (цель 2R, стоп за зоной + 2 п.)")):
        print(f"\n================ {name}")
        show("все", table(real, plac, "all", metric), metric)
        show("цвет зоны", table(real, plac, "role_color", metric), metric)
        show("цвет против роли в тесте", table(real, plac, "color_fit", metric), metric)
        show("главные у краёв VA композита", table(real, plac, "main", metric), metric)
        show("роль в тесте", table(real, plac, "role", metric), metric)
        show("период касания", table(real, plac, "period", metric), metric)
        show("место открытия", table(real, plac, "open_loc", metric), metric)
        show("A принят / отвергнут", table(real, plac, "a_accept", metric), metric)
        show("кто выигрывает к касанию (за / против стороны зоны)", table(real, plac, "with_ctrl", metric), metric)
        show("зона внутри диапазона дня (откат) / за экстремумом", table(real, plac, "range_pos", metric), metric)
        show("активность по Далтону (зона к VA вчера × роль)", table(real, plac, "activity", metric), metric)
        show("зона у уровня вчера (VAH/VAL/POC/High/Low ± 1 п.)", table(real, plac, "confl_any", metric), metric)
        show("выход за IB к касанию", table(real, plac, "re", metric), metric)
        show("IB к среднему", table(real, plac, "ib_rel", metric), metric)
        show("свежесть зоны", table(real, plac, "fresh", metric), metric)


def main(*args):
    cfg = load_config(instrument="ES")
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    if "build" in args or not (OUT / "events.csv").exists():
        real, plac = build()
    else:
        real = pd.read_csv(OUT / "events.csv", parse_dates=["date"])
        plac = pd.read_csv(OUT / "events_placebo.csv", parse_dates=["date"])
    real, plac = enrich(real, split), enrich(plac, split)
    report(real, plac)


if __name__ == "__main__":
    main(*sys.argv[1:])
