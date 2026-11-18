"""Связка автора механически: уровень 30m → прокол и возврат на минутке → вход со стопом за проколом; контекст 30m.

Уровни дня (в ± среднем дневном диапазоне от open): зоны 1.8 (снимок к открытию, ZoneSnap) и вчерашние VAH / VAL / POC /
High / Low (± 0.5 п.). Вход — tpo/m1_entries.py. Контекст — статус дня после последнего закрытого 30m-периода
(scripts.day_status, модель учится на днях до 2025): «покупатель», если P(новый High) − P(новый Low) > 0.15,
«продавец», если < −0.15; плюс простое «кто обновляет принты» (control). Сделка «по контексту» — лонг при покупателе,
шорт при продавце. Фальш — вся карта уровней дня сдвинута на ±U(1.5, 6) п. (3 прогона), правила те же.

Вход: output/z18/es_1m_rth.csv (выгрузка автора ESZ6 1 min RTH: `YYYYMMDD HHMMSS,o,h,l,c,v`, время Europe/Berlin),
снимки output/z18/snap_*.csv, панель output/z18/day_status.csv.
Запуск:  python -m scripts.m1_entries [build]     Результат: output/z18/m1_trades.csv, отчёт в stdout.
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from scripts.day_status import LOCAL, TARGETS, X_of, fit
from scripts.zones18_mp import OUT
from tpo.data import load_bars, load_config
from tpo.m1_entries import result_r, trades_for_day
from tpo.zones18 import day_table, load_snaps

N_PLAC = 3
COLOR = {"CORE": "синяя", "HVN_S": "зелёная", "HVN_R": "красная", "GREY": "серая"}
_G = {}


def load_1m(path):
    m = pd.read_csv(path, header=None, names=["ts", "open", "high", "low", "close", "volume"])
    ts = pd.to_datetime(m.ts, format="%Y%m%d %H%M%S").dt.tz_localize("Europe/Berlin").dt.tz_convert("America/Chicago")
    loc = ts.dt.tz_localize(None)
    mins = loc.dt.hour * 60 + loc.dt.minute
    m = m[(mins >= 510) & (mins < 900)].copy()
    m["date"] = loc[m.index].dt.normalize()
    m["minute"] = mins[m.index] - 510
    return m


def _init(*a):
    _G.update(zip(("m1", "levels"), a))


def _one(args):
    d, run = args
    g = _G["m1"][d]
    lv = _G["levels"][d]
    rng = np.random.default_rng(int(d.strftime("%Y%m%d")) * 10 + run)
    sh = 0.0 if run < 0 else np.round(rng.uniform(1.5, 6.0) * rng.choice([-1, 1]) * 4) / 4
    zones = list(zip(lv.lo + sh, lv.hi + sh))
    out = trades_for_day(g.open.to_numpy(), g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy(),
                         g.volume.to_numpy().astype(float), zones)
    for t in out:
        t.update(date=d, run=run, kind=lv.kind.iloc[t["zi"]])
    return out


def build(cfg, tick):
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    days["rng_avg20"] = (days.high - days.low).where(~days.short_day).rolling(20, min_periods=10).mean().shift(1)
    m = load_1m(OUT / "es_1m_rth.csv")
    m1 = {d: g.sort_values("minute").reset_index(drop=True) for d, g in m.groupby("date")}
    snaps = load_snaps(OUT)
    ok = days[(~days.short_day) & (days.y_short_day == False) & (~days.roll_day) & days.rng_avg20.notna()
              & days.date.isin(set(snaps.day)) & days.date.isin(m1.keys())]
    ok = ok[[len(m1[d]) == 390 for d in ok.date]].set_index("date", drop=False)
    levels = {}
    for d, z in snaps[snaps.day.isin(ok.index)].groupby("day"):
        day = ok.loc[d]
        z = z[((z.lo + z.hi) / 2 - day.open).abs() <= day.rng_avg20]
        lv = pd.DataFrame({"lo": z.lo, "hi": z.hi, "kind": z.kind.map(COLOR)})
        y = pd.DataFrame({"lo": [day.y_vah - .5, day.y_val - .5, day.y_poc - .5, day.y_high - .5, day.y_low - .5],
                          "hi": [day.y_vah + .5, day.y_val + .5, day.y_poc + .5, day.y_high + .5, day.y_low + .5],
                          "kind": ["VAH вчера", "VAL вчера", "POC вчера", "High вчера", "Low вчера"]})
        levels[d] = pd.concat([lv, y], ignore_index=True)
    jobs = [(d, run) for run in range(-1, N_PLAC) for d in levels]
    with Pool(4, initializer=_init, initargs=(m1, levels)) as pool:
        res = pool.map(_one, jobs, chunksize=16)
    tr = pd.DataFrame([t for r in res for t in r])
    tr.to_csv(OUT / "m1_trades.csv", index=False)
    return tr


def add_context(tr, split):
    """Статус после последнего закрытого периода: P(новый High) − P(новый Low) по модели, обученной до split."""
    st = pd.read_csv(OUT / "day_status.csv", parse_dates=["date"])
    for tg in ("ext_up", "ext_dn"):
        clf, cols_, d, X, trn = fit(st, tg, LOCAL, split)
        st["p_" + tg] = clf.predict_proba(X_of(st, LOCAL).reindex(columns=cols_, fill_value=0.0))[:, 1]
    st = st[["date", "k", "p_ext_up", "p_ext_dn", "control"]]
    tr["k"] = tr.minute // 30 - 1                       # последний закрытый 30m-период
    tr = tr.merge(st, on=["date", "k"], how="left")
    dlt = tr.p_ext_up - tr.p_ext_dn
    tr["ctx"] = np.select([tr.k < 0, dlt > 0.15, dlt < -0.15], ["период A (статуса нет)", "покупатель", "продавец"], "нейтрально")
    lng = tr.side == "лонг"
    tr["fit"] = np.select([tr.ctx.isin(["период A (статуса нет)", "нейтрально"]),
                           (lng & (tr.ctx == "покупатель")) | (~lng & (tr.ctx == "продавец"))],
                          ["без фильтра", "по контексту"], "против контекста")
    tr["fit_ctrl"] = np.select([tr.k < 0, tr.control * np.where(lng, 1, -1) > 0, tr.control * np.where(lng, 1, -1) < 0],
                               ["период A", "по принтам", "против принтов"], "принты поровну")
    tr["sample"] = np.where(tr.date < split, "до 2025", "2025+")
    tr["vol"] = np.select([tr.probe_vol >= 2, tr.probe_vol >= 1.2], ["объём прокола ≥ 2× медианы", "1.2–2×"], "< 1.2×")
    for t in (2, 3):
        tr[f"r{t}"] = result_r(tr, t)
    return tr


def show(tr, by, title, min_n=80):
    print(f"\n### {title}")
    print(f"  {'':34s} {'2R: до 2025 · 2025+':>40s} | {'3R: до 2025 · 2025+':>40s} | доля плюсовых (2R)")
    real, plac = tr[tr.run < 0], tr[tr.run >= 0]
    keys = real[by].value_counts().index
    for key in keys:
        cells = []
        for t in ("r2", "r3"):
            c = []
            for smp in ("до 2025", "2025+"):
                a = real[(real[by] == key) & (real["sample"] == smp)][t]
                p = plac[(plac[by] == key) & (plac["sample"] == smp)].groupby("run")[t].mean()
                c.append("        —        " if len(a) < min_n else f"{a.mean():+.2f} ({p.min():+.2f}…{p.max():+.2f})")
            cells.append(" · ".join(c))
        a = real[real[by] == key]
        print(f"  {str(key):34s} {cells[0]:>40s} | {cells[1]:>40s} | {(a.r2 > 0).mean():4.0%}  n={len(a)}")


def main(*args):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    tr = build(cfg, tick) if "build" in args or not (OUT / "m1_trades.csv").exists() else \
        pd.read_csv(OUT / "m1_trades.csv", parse_dates=["date"])
    tr = add_context(tr, split)
    r = tr[tr.run < 0]
    print(f"сделок (реальные уровни): {len(r)} за {r.date.nunique()} дней, риск медиана {r.risk.median():.2f} п.; "
          f"ячейка: средний R (разброс 3 фальш-прогонов со сдвинутыми уровнями); комиссия не учтена")
    tr["all"] = "все входы"
    show(tr, "all", "Все входы")
    show(tr, "fit", "Контекст 30m (статус: P(новый High) − P(новый Low)) — честно только 2025+")
    show(tr, "fit_ctrl", "Кто обновляет принты к входу")
    show(tr, "kind", "Тип уровня")
    show(tr, "vol", "Объём на проколе (макс. минутный объём прокола к медиане 30 минут)")
    tr["fit_kind"] = tr.fit + " · " + np.where(tr.kind.isin(["синяя", "зелёная", "красная", "серая"]), "зона 1.8", "уровень вчера")
    show(tr, "fit_kind", "Контекст × тип уровня")


if __name__ == "__main__":
    main(*sys.argv[1:])
